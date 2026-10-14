"""Read non-secret routing metadata using the saved Open WebUI login."""
import json
from urllib.parse import urlsplit
import urllib.request

from diagnose_model_tools import NoRedirect, core, credentials


def get_config(server, key):
    request = urllib.request.Request(server + "/openai/config", headers={"Authorization": "Bearer " + key})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
        return json.loads(response.read(1024 * 1024))


def main():
    try:
        prefs = core.load_preferences()
        config = get_config(prefs["server_url"], credentials.load_key(prefs["server_url"]))
        report = []
        for index, url in enumerate(config["OPENAI_API_BASE_URLS"]):
            parsed = urlsplit(url)
            item = config.get("OPENAI_API_CONFIGS", {}).get(str(index), {})
            ids = item.get("model_ids", [])
            report.append({"index": index, "host": parsed.hostname, "port": parsed.port,
                           "api_type": item.get("api_type", "chat_completions"),
                           "model_filter_count": len(ids), "explicitly_contains_model": prefs["model_id"] in ids})
        print(json.dumps({"connections": report}, indent=2))
        return 0
    except Exception:
        print(json.dumps({"error": "routing_metadata_unavailable"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
