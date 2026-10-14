"""Synthetic tool-result-to-answer check through the saved studio connection.

Run in a terminal, not Houdini's Python Shell: network requests are blocking.
Each repeat makes at most two model requests. No Houdini imports, scene reads,
tool execution, or history changes. Only counts/outcomes are printed; keys,
response text, tool arguments, and provider continuation state are never logged.
"""
import argparse
import copy
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
from houlm import core, credentials, stream_diagnostics, toolchat


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def payload(model):
    return {
        "model": model, "stream": True, "tool_choice": "auto", "tool_ids": [],
        "params": {"function_calling": "native"},
        "messages": [
            {"role": "system", "content": (
                "This is a synthetic transport diagnostic. First request the supplied "
                "diagnostic_add tool exactly once with a=5 and b=7. After its result "
                "is supplied, answer with exactly the numeric sum and nothing else. "
                "The client supplies a canned result; tools are never executed.")},
            {"role": "user", "content": "Use diagnostic_add for 5 + 7, then report its sum."},
        ],
        "tools": [{"type": "function", "function": {
            "name": "diagnostic_add", "description": "Synthetic check; never executed.",
            "parameters": {"type": "object", "properties": {
                "a": {"type": "integer"}, "b": {"type": "integer"}},
                "required": ["a", "b"], "additionalProperties": False},
        }}],
    }


def _request(opener, url, key, body):
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream",
                 "Authorization": "Bearer " + key}, method="POST")
    report = {}
    try:
        with opener.open(request, timeout=180) as response:
            report["http_status"] = response.status
            raw = response.read(core.MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        report.update(http_status=error.code, error="http_error")
        return report, None
    except Exception:
        report["error"] = "connection_failed"
        return report, None
    report["response_bytes"] = len(raw)
    if len(raw) > core.MAX_RESPONSE_BYTES:
        report["error"] = "response_too_large"
        return report, None
    report["wire"] = stream_diagnostics.analyze(raw)
    try:
        reply = toolchat.parse_reply(raw)
    except core.ProtocolError as error:
        report.update(error=error.code, metrics=error.metrics)
        return report, None
    except Exception:
        report["error"] = "unexpected_response"
        return report, None
    report.update(parsed_tool_calls=len(reply["tool_calls"]),
                  text_characters=len(reply["content"]),
                  argument_characters=sum(len(call["function"]["arguments"])
                                          for call in reply["tool_calls"]))
    return report, reply


def run(server, model, key):
    url = core.normalize_server_url(server) + "/api/chat/completions"
    opener = urllib.request.build_opener(NoRedirect())
    body = payload(model)
    report = {"synthetic_only": True, "tools_executed": 0, "passed": False, "turns": []}
    first, reply = _request(opener, url, key, body)
    report["turns"].append(first)
    if reply is None:
        return report
    calls = reply["tool_calls"]
    matches = (len(calls) == 1 and calls[0]["function"]["name"] == "diagnostic_add"
               and json.loads(calls[0]["function"]["arguments"]) == {"a": 5, "b": 7})
    first["arguments_match_synthetic_request"] = matches
    if not matches:
        first["error"] = "synthetic_tool_request_mismatch"
        return report
    assistant = {"role": "assistant", "content": reply["content"], "tool_calls": copy.deepcopy(calls)}
    if reply.get("reasoning_items"):
        # Preserve opaque provider state for the next turn without inspecting it.
        assistant["reasoning_items"] = copy.deepcopy(reply["reasoning_items"])
    body["messages"].extend([assistant, {"role": "tool", "tool_call_id": calls[0]["id"],
                                       "content": '{"sum":12}'}])
    body["tool_choice"] = "none"
    second, answer = _request(opener, url, key, body)
    report["turns"].append(second)
    if answer is not None:
        second["answer_matches_synthetic_result"] = (
            not answer["tool_calls"] and answer["content"].strip() == "12")
        report["passed"] = second["answer_matches_synthetic_result"]
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, choices=range(1, 6), default=1)
    args = parser.parse_args(argv)
    try:
        preferences = core.load_preferences()
        server, model = preferences["server_url"], preferences["model_id"]
        if not server or not model:
            raise ValueError()
        key = credentials.load_key(server)
        if not key:
            raise ValueError()
    except Exception:
        print(json.dumps({"error": "saved_connection_unavailable"}))
        return 2
    failed = False
    for _ in range(args.repeat):
        report = run(server, model, key)
        print(json.dumps(report, indent=2), flush=True)
        failed |= not report["passed"]
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
