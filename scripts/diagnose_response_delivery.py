"""Measure synthetic response delivery through the two Open WebUI routes.

Run in a terminal, never Houdini's Python Shell. Uses the saved connection and
login, sends synthetic data only, and never executes tools or changes history.
No response content, credentials, URLs, model names or exception text is logged.
"""
import argparse
from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import socket
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from diagnose_model_answer import payload
from houlm import core, credentials, stream_diagnostics, toolchat


ROUTES = {
    "chat": "/api/chat/completions",
    "passthrough": "/openai/chat/completions",
}


def diagnostic_payload(model, full_schema=False):
    if not full_schema:
        return payload(model)
    return toolchat.build_payload(model, [{"role": "user", "content": (
        "This is a synthetic transport diagnostic with no live scene. Request "
        "exactly one read-only scene tool to list the children of /obj. The "
        "client only inspects your response and will never execute the call. "
        "Do not request Python, edits, cooking, rendering or any scene changes.")}],
        current_turn_tool_count=0)


def route_body(body, route):
    # /api/chat/completions consumes WebUI controls before contacting the
    # provider. Its OpenAI passthrough forwards fields directly, so remove
    # only those two controls; standard model/messages/tools fields stay equal.
    if route == "passthrough":
        value = json.loads(body)
        value.pop("params", None)
        value.pop("tool_ids", None)
        return json.dumps(value).encode("utf-8")
    return body


def _terminal(event):
    """Return only a fixed terminal category, never event text."""
    text = event.decode("utf-8", errors="replace").replace("\r\n", "\n")
    data = "\n".join(line[5:].lstrip(" ") for line in text.splitlines()
                     if line.startswith("data:"))
    if data == "[DONE]":
        return "done"
    try:
        value = json.loads(data)
    except (ValueError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    if value.get("type") in ("response.completed", "response.failed", "response.incomplete"):
        return "responses_terminal"
    choices = value.get("choices")
    if isinstance(choices, list) and any(isinstance(choice, dict) and choice.get("finish_reason")
                                         for choice in choices):
        return "chat_finish"
    return None


def probe(server, key, body, route, deadline=45):
    if route not in ROUTES or type(deadline) is not int or not 1 <= deadline <= 60:
        raise ValueError("Invalid probe options.")
    body = route_body(body, route)
    parsed = urlsplit(core.normalize_server_url(server))
    target = parsed.path.rstrip("/") + ROUTES[route]
    started = time.monotonic()
    report = {
        "route": ROUTES[route],
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "synthetic_only": True, "tools_executed": 0, "deadline_seconds": deadline,
        "http_status": 0, "headers_seconds": None, "first_byte_seconds": None,
        "terminal_seconds": None, "terminal_kind": None, "done_seconds": None,
        "eof_seconds": None, "elapsed_seconds": 0, "response_bytes": 0,
        "read_count": 0, "outcome": "connection_failed",
        "request_bytes": len(body),
    }
    connection = None
    response = None
    transport_socket = None
    raw, pending = bytearray(), bytearray()

    def elapsed():
        return round(time.monotonic() - started, 3)

    def remaining():
        seconds = deadline - (time.monotonic() - started)
        if seconds <= 0:
            raise TimeoutError()
        if transport_socket is not None:
            transport_socket.settimeout(seconds)
        return seconds

    try:
        factory = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = factory(parsed.hostname, port=parsed.port, timeout=remaining())
        connection.request("POST", target, body=body, headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json", "Accept": "text/event-stream",
            "Accept-Encoding": "identity", "Connection": "close",
        })
        transport_socket = connection.sock
        remaining()
        response = connection.getresponse()
        report["headers_seconds"] = elapsed()
        report["http_status"] = response.status
        if 300 <= response.status < 400:
            report["outcome"] = "redirect_rejected"
        elif not 200 <= response.status < 300:
            report["outcome"] = "http_error"
        else:
            while True:
                remaining()
                chunk = response.read1(min(65536, core.MAX_RESPONSE_BYTES + 1 - len(raw)))
                report["read_count"] += 1
                if not chunk:
                    report["eof_seconds"] = elapsed()
                    report["outcome"] = "complete" if report["terminal_seconds"] is not None else "eof_without_terminal"
                    break
                if report["first_byte_seconds"] is None:
                    report["first_byte_seconds"] = elapsed()
                raw.extend(chunk)
                pending.extend(chunk)
                if len(raw) > core.MAX_RESPONSE_BYTES:
                    report["outcome"] = "response_too_large"
                    break
                while True:
                    ends = [(pending.find(delimiter), delimiter) for delimiter in (b"\n\n", b"\r\n\r\n")]
                    ends = [(index, delimiter) for index, delimiter in ends if index >= 0]
                    if not ends:
                        break
                    index, delimiter = min(ends, key=lambda item: item[0])
                    kind = _terminal(bytes(pending[:index]))
                    del pending[:index + len(delimiter)]
                    if kind and report["terminal_seconds"] is None:
                        report["terminal_seconds"], report["terminal_kind"] = elapsed(), kind
                    if kind == "done" and report["done_seconds"] is None:
                        report["done_seconds"] = elapsed()
    except (TimeoutError, socket.timeout):
        report["outcome"] = "deadline_exceeded"
    except Exception:
        report["outcome"] = "connection_failed"
    finally:
        if response is not None:
            response.close()
        if connection is not None:
            connection.close()
    report["elapsed_seconds"] = elapsed()
    report["response_bytes"] = len(raw)
    report["wire"] = stream_diagnostics.analyze(bytes(raw))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", choices=("both", *ROUTES), default="both")
    parser.add_argument("--full-schema", action="store_true",
                        help="Use the real panel instructions/tools with a synthetic read-only prompt; no scene/history access or tool execution.")
    parser.add_argument("--deadline", type=int, choices=range(1, 61), default=45,
                        metavar="1..60", help="Maximum socket/request wait per route; default 45 seconds.")
    args = parser.parse_args(argv)
    try:
        preferences = core.load_preferences()
        server, model = preferences["server_url"], preferences["model_id"]
        if not server or not model:
            raise ValueError()
        key = credentials.load_key(server)
        if not key:
            raise ValueError()
        body = json.dumps(diagnostic_payload(model, args.full_schema)).encode("utf-8")
    except Exception:
        print(json.dumps({"error": "saved_connection_unavailable"}), flush=True)
        return 2
    failed = False
    for route in ROUTES if args.route == "both" else (args.route,):
        print(json.dumps({"route": ROUTES[route], "probe_starting_utc":
                          datetime.now(timezone.utc).isoformat(timespec="seconds")}), flush=True)
        report = probe(server, key, body, route, args.deadline)
        report["full_schema"] = args.full_schema
        print(json.dumps(report, indent=2), flush=True)
        failed |= report["outcome"] != "complete"
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
