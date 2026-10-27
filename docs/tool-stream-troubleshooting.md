# Response troubleshooting

Read **Error details** first: UTC timestamp, HTTP status, bytes and stream counters. Correlate with the same request in Open WebUI/LiteLLM. Upstream success does not establish complete delivery to the panel.

| Symptom | Check |
| --- | --- |
| Waiting, no HTTP status or bytes | Response headers have not reached the panel; compare server request timings. |
| HTTP 200, empty reply | The response completed without usable text/tools. |
| Missing/invalid tool arguments | The rejected batch did not execute; inspect the model route. |
| A command receipt followed by failure | Earlier commands may have completed; inspect their results before continuing. |
| Context limit | Narrow the request or start a new chat; local history is retained. |

Inactivity defaults to 10 minutes and is configurable in Connections. Increasing it does not fix stalled delivery. Timeouts neither undo nor replay commands.

## Run synthetic checks

From a source checkout in **PowerShell**, after connecting the panel:

```powershell
$houdiniPython = 'C:/Program Files/Side Effects Software/Houdini 20.5.445/python311/python.exe'
& $houdiniPython scripts/diagnose_response_delivery.py --route chat --full-schema --deadline 45
& $houdiniPython scripts/diagnose_model_answer.py --repeat 3
& $houdiniPython scripts/diagnose_model_tools.py --repeat 3
```

These use the saved route and make real model requests with synthetic data; no scene commands execute. The first probe has a 45-second deadline. The others use per-request socket timeouts, not a total elapsed deadline. `saved_connection_unavailable` means reconnect the panel first.

For affected ChatGPT-provider streams, consult the backend's [optional adapter](https://github.com/Byter3/studio-assistant-backend/blob/main/gateway/README.md). Change one component at a time.

## Retest

> List the immediate children of /obj using scene tools. Do not modify anything or force a cook.

Compare the answer with its receipt. [Recall/edit/resume prompts](houdini-panel-quickstart.md#test-auto-run-command-receipts-and-resume).

Report versions, sanitized Error details and a minimal prompt. Do not attach private HIPs, histories or image assets. [Backend setup](https://github.com/Byter3/studio-assistant-backend/blob/main/docs/server-setup.md).
