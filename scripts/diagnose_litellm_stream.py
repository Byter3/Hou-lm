"""Offline LiteLLM text/tool-stream probe; run inside the LiteLLM container.

This uses synthetic events only. It does not load credentials, contact a model,
read scene data, modify installed packages, or change the running gateway.
Exit status: 0 = all checks pass, 1 = conversion defect, 2 = unavailable probe.
"""

import argparse
import copy
import importlib.metadata
import json
import os
import platform
import sys


def _item(index, arguments):
    return {"id": "fc_houlm_probe_%d" % index, "type": "function_call",
            "call_id": "call_houlm_probe_%d" % index,
            "name": "probe_%d" % index, "arguments": arguments}


def _added(index, arguments=""):
    return {"type": "response.output_item.added", "output_index": index,
            "item": _item(index, arguments)}


def _delta(index, arguments):
    return {"type": "response.function_call_arguments.delta", "output_index": index,
            "item_id": _item(index, "")["id"], "delta": arguments}


def _done(index, arguments):
    return {"type": "response.function_call_arguments.done", "output_index": index,
            "item_id": _item(index, "")["id"], "arguments": arguments}


def _closed(index, arguments):
    return {"type": "response.output_item.done", "output_index": index,
            "item": _item(index, arguments)}


def fixtures():
    """Synthetic provider behaviors, including sparse/interleaved output indices."""
    first, second = '{"value":"alpha"}', '{"value":"beta"}'
    cases = [
        ("arguments_done_only", [_added(1), _done(1, first), _closed(1, first)], {1: first}, False),
        ("output_item_done_only", [_added(1), _closed(1, first)], {1: first}, False),
        ("deltas_then_done", [_added(1), _delta(1, first[:9]), _delta(1, first[9:]),
                              _done(1, first), _closed(1, first)], {1: first}, False),
        ("partial_delta_then_done", [_added(1), _delta(1, first[:9]),
                                     _done(1, first), _closed(1, first)], {1: first}, False),
        ("opening_has_arguments", [_added(1, first), _done(1, first),
                                   _closed(1, first)], {1: first}, False),
        ("interleaved_done_only", [_added(1), _added(3), _done(1, first),
                                  _closed(1, first), _done(3, second),
                                  _closed(3, second)], {1: first, 3: second}, False),
        ("interleaved_deltas", [_added(1), _added(3), _delta(1, first[:9]),
                               _delta(3, second[:9]), _delta(1, first[9:]),
                               _delta(3, second[9:]), _done(1, first), _done(3, second),
                               _closed(1, first), _closed(3, second)], {1: first, 3: second}, False),
        ("empty_terminal_output", [_added(1), _delta(1, first), _done(1, first),
                                   _closed(1, first)], {1: first}, True),
    ]
    for name, events, expected, empty_terminal in cases:
        created = {"type": "response.created", "response": {"id": "resp_houlm_probe"}}
        completed = {"type": "response.completed", "response": {
            "id": "resp_houlm_probe", "status": "completed",
            "output": [] if empty_terminal else [_item(i, arg) for i, arg in expected.items()]}}
        yield name, [created] + events + [completed], {
            "probe_%d" % i: arg for i, arg in expected.items()}


def evaluate(chunks, expected):
    calls, finish_reasons = {}, []
    for chunk in chunks:
        for choice in chunk.choices or []:
            if choice.finish_reason:
                finish_reasons.append(choice.finish_reason)
            for call in choice.delta.tool_calls or []:
                entry = calls.setdefault(call.index, {"name": "", "arguments": ""})
                if call.function.name:
                    # Closing events sometimes repeat identity; this is not name text.
                    if entry["name"] and entry["name"] != call.function.name:
                        entry["name"] += ":conflicting_name"
                    else:
                        entry["name"] = call.function.name
                entry["arguments"] += call.function.arguments or ""
    actual = {entry["name"]: entry["arguments"] for entry in calls.values()}
    args_ok = len(calls) == len(expected) and actual == expected
    finish_ok = finish_reasons == ["tool_calls"]
    return {"passed": args_ok and finish_ok, "arguments_match": args_ok,
            "terminal_finish_matches": finish_ok, "tool_call_count": len(calls),
            "argument_characters": sum(len(e["arguments"]) for e in calls.values()),
            "finish_reason_count": len(finish_reasons)}


def _text_item(index, texts):
    return {"id": "msg_houlm_probe_%d" % index, "type": "message", "role": "assistant",
            "status": "completed", "content": [
                {"type": "output_text", "text": text, "annotations": []} for text in texts]}


def _text_event(kind, index, text, content_index=0):
    event = {"type": "response.output_text." + kind, "output_index": index,
             "item_id": _text_item(index, [""])["id"], "content_index": content_index}
    event["delta" if kind == "delta" else "text"] = text
    return event


def text_fixtures():
    """Public answer text only; synthetic completed snapshots must not be lost."""
    answer = "Probe answer: 12."
    item = _text_item(2, [answer])
    added = {"type": "response.output_item.added", "output_index": 2,
             "item": _text_item(2, [""])}
    done = _text_event("done", 2, answer)
    closed = {"type": "response.output_item.done", "output_index": 2, "item": item}
    cases = [
        ("text_done_only", [added, done, closed], [item], answer),
        ("text_item_done_only", [added, closed], [item], answer),
        ("text_completed_only", [], [item], answer),
        ("text_deltas_then_done", [added, _text_event("delta", 2, answer[:7]),
                                  _text_event("delta", 2, answer[7:]), done, closed], [item], answer),
        ("text_partial_delta_then_done", [added, _text_event("delta", 2, answer[:7]),
                                         done, closed], [item], answer),
        ("text_repeated_snapshots", [added, done, done, closed, closed], [item], answer),
        ("text_opening_has_content", [{**added, "item": item}, done, closed], [item], answer),
        ("text_empty_terminal_output", [added, done, closed], [], answer),
    ]
    second = " Next part."
    multipart = _text_item(2, [answer, second])
    cases.append(("text_multiple_content_parts", [added, done,
                  _text_event("done", 2, second, content_index=1),
                  {**closed, "item": multipart}], [multipart], answer + second))
    later = _text_item(5, [second])
    cases.append(("text_sparse_message_items", [added, done, closed,
                  {"type": "response.output_item.added", "output_index": 5,
                   "item": _text_item(5, [""])}, _text_event("done", 5, second),
                  {"type": "response.output_item.done", "output_index": 5, "item": later}],
                  [item, later], answer + second))
    for name, events, output, expected in cases:
        created = {"type": "response.created", "response": {"id": "resp_houlm_text_probe"}}
        completed = {"type": "response.completed", "response": {
            "id": "resp_houlm_text_probe", "status": "completed", "output": output}}
        yield name, [created] + events + [completed], expected


def evaluate_text(chunks, expected):
    text, finish_reasons, tool_calls = "", [], 0
    for chunk in chunks:
        for choice in chunk.choices or []:
            if choice.finish_reason:
                finish_reasons.append(choice.finish_reason)
            content = getattr(choice.delta, "content", None)
            if isinstance(content, str):
                text += content
            tool_calls += len(getattr(choice.delta, "tool_calls", None) or [])
    text_ok = text == expected
    finish_ok = finish_reasons == ["stop"]
    return {"passed": text_ok and finish_ok and tool_calls == 0,
            "text_matches": text_ok, "terminal_finish_matches": finish_ok,
            "text_characters": len(text), "expected_text_characters": len(expected),
            "tool_call_count": tool_calls, "finish_reason_count": len(finish_reasons)}


def probe(converter_class, normalizer_class):
    checks = []
    cases = [(case, evaluate) for case in fixtures()]
    cases.extend((case, evaluate_text) for case in text_fixtures())
    for (name, events, expected), evaluate_case in cases:
        for layer in ("converter", "chatgpt_normalizer"):
            try:
                converter = converter_class(iter(()), sync_stream=True)
                chunks = (converter.chunk_parser(copy.deepcopy(event)) for event in events)
                if layer == "chatgpt_normalizer":
                    chunks = normalizer_class(iter(chunks))
                result = evaluate_case(chunks, expected)
            except Exception as exc:
                # No upstream exception text or environment details in the report.
                result = {"passed": False, "exception_type": type(exc).__name__}
            checks.append({"case": name, "layer": layer, **result})
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print machine-readable results")
    args = parser.parse_args(argv)
    # Prevent LiteLLM from fetching its model-cost catalog during import.
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    report = {"probe": "houlm-litellm-stream-v2", "python": platform.python_version(),
              "synthetic_events_only": True}
    try:
        report["litellm"] = importlib.metadata.version("litellm")
        from litellm.completion_extras.litellm_responses_transformation.transformation import (
            OpenAiResponsesToChatCompletionStreamIterator,
        )
        from litellm.llms.chatgpt.chat.streaming_utils import ChatGPTToolCallNormalizer
        report["checks"] = probe(OpenAiResponsesToChatCompletionStreamIterator, ChatGPTToolCallNormalizer)
        report["passed"] = all(check["passed"] for check in report["checks"])
        code = 0 if report["passed"] else 1
    except Exception as exc:
        report.update(passed=False, unavailable=True, exception_type=type(exc).__name__)
        code = 2
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("Hou-lm offline text/tool-stream probe")
        print("LiteLLM: %s; Python: %s" % (report.get("litellm", "not installed"), report["python"]))
        print("Synthetic events only; no credentials or model requests.")
        for check in report.get("checks", []):
            print("%s  %s / %s" % ("PASS" if check["passed"] else "FAIL", check["layer"], check["case"]))
        if report.get("unavailable"):
            print("Probe unavailable: %s" % report["exception_type"])
        print("RESULT: %s" % ("PASS" if report["passed"] else "FAIL"))
    return code


if __name__ == "__main__":
    sys.exit(main())
