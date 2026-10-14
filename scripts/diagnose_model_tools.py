"""Synthetic transport check. Uses saved login; never imports hou or runs tools.

Run from a terminal, not Houdini's Python Shell (network requests are blocking).
No response bodies, generated text, tool arguments, or keys are printed/saved.
"""
import argparse
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "houdini" / "python3.11libs"))
from houlm import core, credentials, toolchat


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def payload(model, serial=False):
    body = {
        "model": model, "stream": True, "tool_choice": "auto", "tool_ids": [],
        "params": {"function_calling": "native"},
        "messages": [
            {"role": "system", "content": "This is a synthetic tool transport diagnostic. Request the provided tools with the exact arguments the user supplies. Do not answer in prose. Tools will not be executed."},
            {"role": "user", "content": 'Request diagnostic_echo with {"text":"probe"}, diagnostic_add with {"a":5,"b":7}, and diagnostic_flag with {"enabled":true}.'},
        ],
        "tools": [
            {"type": "function", "function": {
                "name": name, "description": "Synthetic transport check; never executed.",
                "parameters": {"type": "object", "properties": properties,
                               "required": list(properties), "additionalProperties": False},
            }} for name, properties in (
                ("diagnostic_echo", {"text": {"type": "string"}}),
                ("diagnostic_add", {"a": {"type": "integer"}, "b": {"type": "integer"}}),
                ("diagnostic_flag", {"enabled": {"type": "boolean"}}),
            )
        ],
    }
    if serial:
        body["parallel_tool_calls"] = False
    return body


def run(server, model, key, serial=False):
    from houlm import stream_diagnostics
    request = urllib.request.Request(
        core.normalize_server_url(server) + "/api/chat/completions",
        data=json.dumps(payload(model, serial)).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream",
                 "Authorization": "Bearer " + key}, method="POST")
    opener = urllib.request.build_opener(NoRedirect())
    report = {"synthetic_only": True, "tools_executed": 0,
              "parallel_tool_calls_disabled": serial}
    try:
        with opener.open(request, timeout=180) as response:
            report["http_status"] = response.status
            raw = response.read(core.MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        report.update(http_status=error.code, error="http_error")
        return report
    except Exception:
        report["error"] = "connection_failed"
        return report
    report["response_bytes"] = len(raw)
    if len(raw) > core.MAX_RESPONSE_BYTES:
        report["error"] = "response_too_large"
        return report
    report["wire"] = stream_diagnostics.analyze(raw)
    try:
        reply = toolchat.parse_reply(raw)
    except core.ProtocolError as error:
        report.update(error=error.code, metrics=error.metrics)
    except Exception:
        report["error"] = "unexpected_response"
    else:
        calls = reply["tool_calls"]
        report.update(parsed_tool_calls=len(calls),
                      argument_characters=[len(c["function"]["arguments"]) for c in calls])
        expected = {"diagnostic_echo": {"text": "probe"},
                    "diagnostic_add": {"a": 5, "b": 7},
                    "diagnostic_flag": {"enabled": True}}
        report["arguments_match_synthetic_request"] = bool(calls) and all(
            c["function"]["name"] in expected and
            json.loads(c["function"]["arguments"]) == expected[c["function"]["name"]]
            for c in calls)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", action="store_true", help="Compare with parallel_tool_calls=false.")
    parser.add_argument("--compare", action="store_true", help="Test both default and disabled parallel calls.")
    parser.add_argument("--repeat", type=int, choices=range(1, 6), default=1)
    args = parser.parse_args()
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
        for serial in (False, True) if args.compare else (args.serial,):
            report = run(server, model, key, serial)
            print(json.dumps(report, indent=2), flush=True)
            failed |= bool(report.get("error") or not report.get("arguments_match_synthetic_request"))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
