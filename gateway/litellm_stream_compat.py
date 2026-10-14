"""Version-checked ChatGPT Responses-to-Chat stream repair.

Opt-in gateway compatibility adapter; never imported by the Houdini panel.
It changes only ChatGPTConfig.post_stream_processing, and only wraps that
provider's Responses iterator. Other providers retain upstream behavior.
"""

import argparse
import copy
import hashlib
import importlib.metadata
import inspect
import json
import os
import sys


SUPPORTED = {
    "1.83.14": (
        "2615dff002aa438c2bba297d9319e354aa793647bd85f5a8f63c3d6e4238d9b0",
        "62013842766b9a87f73679bfb70cc0933333e960ffd31c403aa36e53529bff3b",
        "e14978d1a4c715b5d94cb537692b3f027326bf13aefea836a179ce83ff9e2728",
    ),
    "1.103.2": (
        "2615dff002aa438c2bba297d9319e354aa793647bd85f5a8f63c3d6e4238d9b0",
        "77a30f2a14c5f0fe95c2f0673dcbd948ffa688031755d53afa7816ba9a5b1e91",
        "d31228d6acd431a6765966bbf6d36d5ebe81cfb08e75afbea44313eae4050b6c",
    ),
}
MAX_CALLS = 128
MAX_ARGUMENT_CHARACTERS = 1024 * 1024
MAX_TOTAL_ARGUMENT_CHARACTERS = 4 * 1024 * 1024
MAX_TEXT_PARTS = 128
MAX_TEXT_CHARACTERS = 1024 * 1024
MAX_TOTAL_TEXT_CHARACTERS = 4 * 1024 * 1024


class CompatibilityError(RuntimeError):
    """An unsupported installation; do not silently start an unpatched server."""


class ToolStreamError(RuntimeError):
    """Inconsistent provider events; messages deliberately omit argument data."""


def _check(condition, code):
    if not condition:
        raise ToolStreamError("Hou-lm tool stream rejected: " + code)


def _imports():
    from litellm.completion_extras.litellm_responses_transformation.transformation import (
        OpenAiResponsesToChatCompletionStreamIterator,
    )
    from litellm.llms.chatgpt.chat.transformation import ChatGPTConfig
    return OpenAiResponsesToChatCompletionStreamIterator, ChatGPTConfig


def verify_installation():
    version = importlib.metadata.version("litellm")
    if version not in SUPPORTED:
        raise CompatibilityError("Unsupported LiteLLM version: " + version)
    converter_class, config_class = _imports()
    post = config_class.post_stream_processing
    if getattr(post, "_houlm_stream_repair", False):
        post = post._houlm_original
    functions = (post, converter_class.chunk_parser,
                 converter_class.translate_responses_chunk_to_openai_stream)
    actual = tuple(hashlib.sha256(inspect.getsource(fn).replace("\r\n", "\n").encode()).hexdigest()
                   for fn in functions)
    if actual != SUPPORTED[version]:
        raise CompatibilityError("LiteLLM source differs from the verified build")
    return version, converter_class, config_class


class ResponsesToolStreamRepair:
    """Per-request state around an installed iterator's original chunk_parser.

    Track emitted arguments and assistant text by their Responses identities.
    Completed snapshots may supply a missing suffix, never replace earlier data.
    """

    def __init__(self, original_parser):
        self.original_parser = original_parser
        self.calls = {}
        self.call_ids = {}
        self.response_id = None
        self.total_arguments = 0
        self.text_parts = {}
        self.text_item_ids = {}
        self.text_output_ids = {}
        self.completed_text_items = set()
        self.total_text = 0
        self.last_text_key = None
        self.completed = False

    def _base(self, event=None):
        chunk = self.original_parser(event or {"type": "houlm.empty"})
        if self.response_id is None:
            self.response_id = chunk.id
        else:
            chunk.id = self.response_id
        return chunk

    def _chunk(self, calls=(), event=None, text=None):
        chunk = self._base(event)
        if calls:
            chunk.choices[0].delta.tool_calls = list(calls)
        if text is not None:
            chunk.choices[0].delta.content = text
        return chunk

    def _index(self, event):
        index = event.get("output_index")
        _check(isinstance(index, int) and not isinstance(index, bool) and index >= 0,
               "invalid_output_index")
        return index

    def _text(self, text):
        _check(isinstance(text, str), "arguments_not_text")
        _check(len(text) <= MAX_ARGUMENT_CHARACTERS, "arguments_too_large")
        return text

    def _identity(self, call, event, item=None):
        item_id = event.get("item_id")
        if item_id is not None:
            _check(isinstance(item_id, str) and bool(item_id), "invalid_item_identity")
            _check(call["item_id"] is None or item_id == call["item_id"], "item_identity_changed")
            call["item_id"] = item_id
        if item is not None:
            _check(item.get("call_id") == call["call_id"] and
                   item.get("name") == call["name"], "call_identity_changed")
            if item.get("id") is not None:
                self._identity(call, {"item_id": item["id"]})

    def _create(self, index, item):
        _check(len(self.calls) < MAX_CALLS, "too_many_calls")
        _check(index not in self.text_output_ids, "output_item_type_changed")
        for key in ("call_id", "name"):
            _check(isinstance(item.get(key), str) and bool(item[key]), "missing_call_identity")
        _check(item.get("id") is None or (isinstance(item["id"], str) and bool(item["id"])),
               "invalid_item_identity")
        _check(item["call_id"] not in self.call_ids, "call_id_reused")
        # Let this exact installed LiteLLM version encode provider call IDs and
        # metadata; only the streaming lifecycle is replaced here.
        template_item = dict(item, arguments="")
        chunk = self._base({"type": "response.output_item.added", "output_index": index,
                            "item": template_item})
        templates = chunk.choices[0].delta.tool_calls
        _check(bool(templates) and len(templates) == 1, "upstream_identity_conversion_failed")
        call = {"index": len(self.calls), "call_id": item["call_id"], "item_id": item.get("id"),
                "name": item["name"], "arguments": "", "final": False, "announced": False,
                "template": templates[0]}
        self.calls[index] = call
        self.call_ids[item["call_id"]] = index
        return call

    def _existing(self, event):
        index = self._index(event)
        _check(index in self.calls, "arguments_without_call_identity")
        call = self.calls[index]
        self._identity(call, event)
        return call

    def _append(self, call, text):
        self._text(text)
        _check(len(call["arguments"]) + len(text) <= MAX_ARGUMENT_CHARACTERS, "arguments_too_large")
        _check(self.total_arguments + len(text) <= MAX_TOTAL_ARGUMENT_CHARACTERS,
               "stream_arguments_too_large")
        call["arguments"] += text
        self.total_arguments += len(text)

    def _complete(self, call, full):
        full = self._text(full)
        _check(full.startswith(call["arguments"]), "completed_arguments_conflict")
        _check(not call["final"] or full == call["arguments"], "completed_arguments_changed")
        suffix = full[len(call["arguments"]):]
        self._append(call, suffix)
        call["final"] = True
        self._validate_json(call)
        return suffix

    @staticmethod
    def _validate_json(call):
        try:
            decoded = json.loads(call["arguments"])
        except (ValueError, TypeError):
            raise ToolStreamError("Hou-lm tool stream rejected: invalid_completed_arguments") from None
        _check(isinstance(decoded, dict), "completed_arguments_not_object")

    def _emit(self, call, arguments):
        if call["announced"] and not arguments:
            return []
        tc = copy.deepcopy(call["template"])
        tc.index = call["index"]
        tc.function.arguments = arguments
        if call["announced"]:
            tc.id = None
            tc.function.name = None
        call["announced"] = True
        return [tc]

    def _item(self, event, completed):
        index = self._index(event)
        item = event["item"]
        if index in self.calls:
            call = self.calls[index]
            self._identity(call, event, item)
        else:
            call = self._create(index, item)
        arguments = self._text(item.get("arguments", ""))
        if completed:
            suffix = self._complete(call, arguments)
        else:
            # Repeated opening metadata must not append duplicate argument text.
            _check(arguments.startswith(call["arguments"]) or not arguments,
                   "opening_arguments_conflict")
            suffix = arguments[len(call["arguments"]):] if arguments else ""
            _check(not call["final"] or not suffix, "arguments_after_completion")
            self._append(call, suffix)
        return self._emit(call, suffix)

    def _message_identity(self, index, item_id):
        _check(isinstance(item_id, str) and 0 < len(item_id) <= 1024,
               "invalid_text_item_identity")
        _check(index not in self.calls, "output_item_type_changed")
        _check(self.text_output_ids.get(index, item_id) == item_id, "text_item_identity_changed")
        _check(self.text_item_ids.get(item_id, index) == index, "text_item_identity_reused")
        _check(index in self.text_output_ids or len(self.text_output_ids) < MAX_TEXT_PARTS,
               "too_many_text_items")
        self.text_output_ids[index] = item_id
        self.text_item_ids[item_id] = index

    def _text_part(self, event):
        index = self._index(event)
        content_index = event.get("content_index")
        _check(type(content_index) is int and content_index >= 0, "invalid_content_index")
        self._message_identity(index, event.get("item_id"))
        key = (index, content_index)
        if key not in self.text_parts:
            _check(index not in self.completed_text_items, "text_after_item_completion")
            _check(len(self.text_parts) < MAX_TEXT_PARTS, "too_many_text_parts")
            self.text_parts[key] = {"text": "", "final": False}
        return key, self.text_parts[key]

    def _append_text(self, key, part, text):
        _check(isinstance(text, str), "text_not_string")
        _check(len(part["text"]) + len(text) <= MAX_TEXT_CHARACTERS, "text_too_large")
        _check(self.total_text + len(text) <= MAX_TOTAL_TEXT_CHARACTERS, "stream_text_too_large")
        if text:
            # Chat Completions has one flat content stream. A late suffix for
            # an earlier part cannot be inserted before already emitted text.
            _check(self.last_text_key is None or key >= self.last_text_key, "text_parts_out_of_order")
            self.last_text_key = key
        part["text"] += text
        self.total_text += len(text)
        return text

    def _text_snapshot(self, event, full, completed):
        key, part = self._text_part(event)
        _check(isinstance(full, str), "text_not_string")
        _check(len(full) <= MAX_TEXT_CHARACTERS, "text_too_large")
        _check(full.startswith(part["text"]), "completed_text_conflict")
        _check(not part["final"] or full == part["text"], "completed_text_changed")
        suffix = self._append_text(key, part, full[len(part["text"]):])
        if completed:
            part["final"] = True
        return suffix

    def _message(self, event, completed):
        index, item = self._index(event), event["item"]
        _check(item.get("role") == "assistant", "message_role_not_assistant")
        if completed:
            _check(item.get("status") in (None, "completed"), "message_not_completed")
        item_id = item.get("id")
        self._message_identity(index, item_id)
        content = item.get("content", [])
        _check(isinstance(content, list), "invalid_message_content")
        _check(len(content) <= MAX_TEXT_PARTS, "too_many_text_parts")
        suffixes = []
        for content_index, part in enumerate(content):
            _check(isinstance(part, dict), "invalid_message_content_part")
            if part.get("type") == "output_text":
                suffixes.append(self._text_snapshot(
                    {"output_index": index, "content_index": content_index, "item_id": item_id},
                    part.get("text"), completed))
            # Reasoning, refusals, annotations and other output types are not
            # ordinary answer text; never reconstruct an answer from them.
        if completed:
            for key in self.text_parts:
                if key[0] == index:
                    _check(key[1] < len(content) and content[key[1]].get("type") == "output_text",
                           "completed_text_part_missing")
            self.completed_text_items.add(index)
        return "".join(suffixes)

    def parse(self, event):
        _check(isinstance(event, dict), "event_not_object")
        _check(not self.completed, "event_after_completion")
        kind = event.get("type")
        if hasattr(kind, "value"):
            kind = kind.value
        if kind in ("response.failed", "response.incomplete", "error"):
            raise ToolStreamError("Hou-lm tool stream rejected: upstream_response_not_completed")
        if kind == "response.output_text.delta":
            key, part = self._text_part(event)
            text = event.get("delta")
            _check(not part["final"] or not text, "text_after_completion")
            return self._chunk(event=event, text=self._append_text(key, part, text))
        if kind == "response.output_text.done":
            return self._chunk(event=event, text=self._text_snapshot(event, event.get("text"), True))
        if kind in ("response.content_part.added", "response.content_part.done"):
            part = event.get("part")
            if isinstance(part, dict) and part.get("type") == "output_text":
                return self._chunk(event=event, text=self._text_snapshot(
                    event, part.get("text"), kind.endswith(".done")))
        if kind in ("response.output_item.added", "response.output_item.done"):
            item = event.get("item", {})
            if isinstance(item, dict) and item.get("type") == "function_call":
                return self._chunk(self._item(event, kind.endswith(".done")))
            if isinstance(item, dict) and item.get("type") == "message":
                return self._chunk(event=event, text=self._message(event, kind.endswith(".done")))
        elif kind == "response.function_call_arguments.delta":
            call = self._existing(event)
            text = self._text(event.get("delta"))
            _check(not call["final"] or not text, "arguments_after_completion")
            self._append(call, text)
            return self._chunk(self._emit(call, text))
        elif kind == "response.function_call_arguments.done":
            call = self._existing(event)
            return self._chunk(self._emit(call, self._complete(call, event.get("arguments"))))
        elif kind == "response.completed":
            response = event.get("response", {})
            _check(isinstance(response, dict), "invalid_completed_response")
            _check(response.get("status") in (None, "completed"), "upstream_response_not_completed")
            output = response.get("output", [])
            _check(isinstance(output, list), "invalid_completed_output")
            tail_calls, tail_text = [], []
            for index, item in enumerate(output):
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "function_call":
                    actual_index = self.call_ids.get(item.get("call_id"), index)
                    tail_calls.extend(self._item({"output_index": actual_index, "item": item}, True))
                elif item.get("type") == "message":
                    actual_index = self.text_item_ids.get(item.get("id"), index)
                    tail_text.append(self._message({"output_index": actual_index, "item": item}, True))
            for call in self.calls.values():
                self._validate_json(call)
            chunk = self._chunk(tail_calls, event, text="".join(tail_text))
            if self.calls:
                chunk.choices[0].finish_reason = "tool_calls"
            self.completed = True
            return chunk
        return self._base(event)


class _CompletionCheckedIterator:
    """Prevent CustomStreamWrapper from converting unexpected EOF into success."""

    def __init__(self, source, repair):
        self.source = source
        self.repair = repair

    def __getattr__(self, name):
        return getattr(self.source, name)

    def __iter__(self):
        iter(self.source)
        return self

    def __next__(self):
        try:
            return next(self.source)
        except StopIteration:
            _check(self.repair.completed, "upstream_stream_ended_before_completion")
            raise

    def __aiter__(self):
        self.source.__aiter__()
        return self

    async def __anext__(self):
        try:
            return await self.source.__anext__()
        except StopAsyncIteration:
            _check(self.repair.completed, "upstream_stream_ended_before_completion")
            raise


def attach(stream, converter_class):
    """Attach only to the known Responses iterator before stream consumption."""
    converter = getattr(stream, "completion_stream", None)
    if isinstance(converter, _CompletionCheckedIterator):
        return stream
    if not isinstance(converter, converter_class):
        raise CompatibilityError("Unexpected ChatGPT stream wrapper; repair was not applied")
    if getattr(converter, "_houlm_stream_repair", False):
        return stream
    repair = ResponsesToolStreamRepair(converter.chunk_parser)
    converter.chunk_parser = repair.parse
    converter._houlm_stream_repair = True
    stream.completion_stream = _CompletionCheckedIterator(converter, repair)
    # Our iterator already emits sequential indexes; the upstream normalizer
    # would reassign interleaved deltas to the most recently opened call.
    return stream


def self_check(converter_class):
    first, second = '{"value":"alpha"}', '{"value":"beta"}'
    events = []
    for i in (1, 3):
        events.append({"type": "response.output_item.added", "output_index": i,
                       "item": {"type": "function_call", "id": "fc_%d" % i,
                                "call_id": "call_%d" % i, "name": "probe_%d" % i,
                                "arguments": ""}})
    events.extend([
        {"type": "response.function_call_arguments.delta", "output_index": 3, "delta": second[:9]},
        {"type": "response.function_call_arguments.done", "output_index": 1, "arguments": first},
        {"type": "response.function_call_arguments.done", "output_index": 3, "arguments": second},
        {"type": "response.function_call_arguments.done", "output_index": 1, "arguments": first},
        {"type": "response.completed", "response": {"id": "resp_probe", "output": []}},
    ])
    adapter = ResponsesToolStreamRepair(converter_class(iter(()), sync_stream=True).chunk_parser)
    actual, finishes = {}, []
    for event in events:
        chunk = adapter.parse(event)
        choice = chunk.choices[0]
        if choice.finish_reason:
            finishes.append(choice.finish_reason)
        for tc in choice.delta.tool_calls or []:
            actual[tc.index] = actual.get(tc.index, "") + (tc.function.arguments or "")
    if actual != {0: first, 1: second} or finishes != ["tool_calls"]:
        raise CompatibilityError("Synthetic tool-stream self-check failed")
    answer = "Synthetic final answer."
    item = {"type": "message", "id": "msg_probe", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": answer}]}
    final = {"type": "response.completed", "response": {"id": "resp_text", "output": [item]}}
    for prefix in ("", answer[:7], answer):
        adapter = ResponsesToolStreamRepair(converter_class(iter(()), sync_stream=True).chunk_parser)
        events = [
            {"type": "response.output_text.delta", "output_index": 2, "content_index": 0,
             "item_id": "msg_probe", "delta": prefix},
            {"type": "response.output_text.done", "output_index": 2, "content_index": 0,
             "item_id": "msg_probe", "text": answer},
            {"type": "response.output_item.done", "output_index": 2, "item": item}, final,
        ]
        chunks = [adapter.parse(event) for event in events]
        if ("".join(chunk.choices[0].delta.content or "" for chunk in chunks) != answer
                or chunks[-1].choices[0].finish_reason != "stop"):
            raise CompatibilityError("Synthetic text-stream self-check failed")
    adapter = ResponsesToolStreamRepair(converter_class(iter(()), sync_stream=True).chunk_parser)
    if adapter.parse(final).choices[0].delta.content != answer:
        raise CompatibilityError("Synthetic terminal-text self-check failed")


def install():
    """Explicit process-local opt-in; never overwrites LiteLLM package files."""
    version, converter_class, config_class = verify_installation()
    if getattr(config_class.post_stream_processing, "_houlm_stream_repair", False):
        return version
    self_check(converter_class)
    original = config_class.post_stream_processing

    def post_stream_processing(self, stream):
        return attach(stream, converter_class)

    post_stream_processing._houlm_stream_repair = True
    post_stream_processing._houlm_original = original
    config_class.post_stream_processing = post_stream_processing
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", required=True,
                        help="Check source fingerprints and synthetic conversion without changing packages")
    parser.parse_args()
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    try:
        version, converter, _ = verify_installation()
        self_check(converter)
    except Exception as exc:
        print("Hou-lm stream compatibility check failed (%s)." % type(exc).__name__, file=sys.stderr)
        return 1
    print("Hou-lm stream compatibility check passed for LiteLLM " + version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
