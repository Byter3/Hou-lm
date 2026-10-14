"""Content-free wire diagnostics; never reconstruct or execute tool calls.

Only fixed counter names and integer values leave this module. Argument sizes
describe fields on the wire, including envelopes the chat parser does not use;
they are not proof that a complete, valid call was delivered.
"""

import json


MAX_SCAN_BYTES = 4 * 1024 * 1024
METRIC_NAMES = (
    "wire_event_count", "wire_done_markers", "wire_json_errors", "wire_scan_truncated",
    "wire_chat_delta_events", "wire_chat_message_events", "wire_unknown_events",
    "wire_delta_content_chars", "wire_message_content_chars",
    "wire_chat_reasoning_events", "wire_chat_refusal_events",
    "wire_finish_stop", "wire_finish_tool_calls", "wire_finish_length",
    "wire_finish_content_filter", "wire_finish_other",
    "wire_delta_argument_fields", "wire_delta_argument_chars",
    "wire_message_argument_fields", "wire_message_argument_chars",
    "wire_other_choice_argument_chars", "wire_responses_events",
    "wire_responses_delta_chars", "wire_responses_done_chars",
    "wire_responses_item_argument_chars", "wire_responses_completed_argument_chars",
    "wire_responses_text_delta_chars", "wire_responses_text_done_chars",
    "wire_responses_completed_text_chars", "wire_responses_completed_reasoning_items",
    "wire_responses_incomplete_events", "wire_responses_failed_events",
)


def _arguments(calls):
    fields, characters = 0, 0
    if isinstance(calls, list):
        for call in calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if isinstance(function, dict) and isinstance(function.get("arguments"), str):
                fields += 1
                characters += len(function["arguments"])
    return fields, characters


def _length(value):
    return len(value) if isinstance(value, str) else 0


def _content_length(content):
    """Count only ordinary text locations, without rendering other payloads."""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(_length(part.get("text")) for part in content
                   if isinstance(part, dict) and part.get("type") in ("text", "output_text"))
    return 0


def _has_reasoning(envelope):
    # Only presence is recorded. In particular, encrypted reasoning, signatures
    # and reasoning text never become user-facing diagnostics.
    return bool(_length(envelope.get("reasoning_content"))
                or _length(envelope.get("reasoning"))
                or (isinstance(envelope.get("reasoning_items"), list)
                    and envelope["reasoning_items"]))


def _item_arguments(item):
    if isinstance(item, dict) and item.get("type") == "function_call":
        return _length(item.get("arguments"))
    return 0


def _chunk(chunk, counters):
    counters["wire_event_count"] += 1
    if not isinstance(chunk, dict):
        counters["wire_unknown_events"] += 1
        return
    recognized = False
    choices = chunk.get("choices")
    if isinstance(choices, list):
        recognized = True
        for number, choice in enumerate(choices):
            if not isinstance(choice, dict):
                continue
            if not number and choice.get("finish_reason") is not None:
                reason = choice["finish_reason"]
                label = reason if isinstance(reason, str) and reason in (
                    "stop", "tool_calls", "length", "content_filter") else "other"
                counters["wire_finish_" + label] += 1
            for field, label in (("delta", "delta"), ("message", "message")):
                envelope = choice.get(field)
                if not isinstance(envelope, dict):
                    continue
                fields, characters = _arguments(envelope.get("tool_calls"))
                if number:
                    counters["wire_other_choice_argument_chars"] += characters
                else:
                    counters["wire_chat_%s_events" % label] += 1
                    counters["wire_%s_argument_fields" % label] += fields
                    counters["wire_%s_argument_chars" % label] += characters
                    counters["wire_%s_content_chars" % label] += _content_length(envelope.get("content"))
                    counters["wire_chat_reasoning_events"] += int(_has_reasoning(envelope))
                    counters["wire_chat_refusal_events"] += int(bool(_length(envelope.get("refusal"))))
    event_type = chunk.get("type")
    # Do not echo arbitrary event names. Responses envelopes remain diagnostic
    # only: the panel speaks Chat Completions and cannot dispatch these objects.
    if isinstance(event_type, str) and event_type.startswith("response."):
        recognized = True
        counters["wire_responses_events"] += 1
        if event_type == "response.function_call_arguments.delta":
            counters["wire_responses_delta_chars"] += _length(chunk.get("delta"))
        elif event_type == "response.function_call_arguments.done":
            counters["wire_responses_done_chars"] += _length(chunk.get("arguments"))
        elif event_type in ("response.output_item.added", "response.output_item.done"):
            counters["wire_responses_item_argument_chars"] += _item_arguments(chunk.get("item"))
        elif event_type == "response.output_text.delta":
            counters["wire_responses_text_delta_chars"] += _length(chunk.get("delta"))
        elif event_type == "response.output_text.done":
            counters["wire_responses_text_done_chars"] += _length(chunk.get("text"))
        elif event_type == "response.incomplete":
            counters["wire_responses_incomplete_events"] += 1
        elif event_type == "response.failed":
            counters["wire_responses_failed_events"] += 1
        elif event_type == "response.completed":
            response = chunk.get("response")
            output = response.get("output") if isinstance(response, dict) else None
            if isinstance(output, list):
                counters["wire_responses_completed_argument_chars"] += sum(_item_arguments(item) for item in output)
                for item in output:
                    if isinstance(item, dict):
                        if item.get("type") == "message":
                            counters["wire_responses_completed_text_chars"] += _content_length(item.get("content"))
                        elif item.get("type") == "reasoning":
                            counters["wire_responses_completed_reasoning_items"] += 1
    if not recognized:
        counters["wire_unknown_events"] += 1


def analyze(raw):
    """Inspect bounded JSON/SSE bytes and return fixed numeric counters only."""
    counters = dict.fromkeys(METRIC_NAMES, 0)
    if type(raw) not in (bytes, bytearray, memoryview):
        return counters
    if len(raw) > MAX_SCAN_BYTES:
        counters["wire_scan_truncated"] = 1
        return counters
    try:
        text = bytes(raw).decode("utf-8-sig")
    except UnicodeError:
        counters["wire_json_errors"] = 1
        return counters
    try:
        payload = json.loads(text)
    except (ValueError, RecursionError):
        pass
    else:
        _chunk(payload, counters)
        return counters
    for event in text.replace("\r\n", "\n").replace("\r", "\n").split("\n\n"):
        data = "\n".join(line[5:].lstrip(" ") for line in event.splitlines() if line.startswith("data:"))
        if not data:
            continue
        if data == "[DONE]":
            counters["wire_done_markers"] += 1
            continue
        try:
            payload = json.loads(data)
        except (ValueError, RecursionError):
            counters["wire_json_errors"] += 1
            continue
        _chunk(payload, counters)
    return counters
