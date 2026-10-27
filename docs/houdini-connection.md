# Connection and tool reference

[Backend setup](https://github.com/Byter3/studio-assistant-backend/blob/main/docs/server-setup.md) · [Panel controls](houdini-panel-quickstart.md) · [Architecture diagram](../README.md#shared-backend)

## Service

The panel uses Open WebUI's `/api/models` and streamed `/api/chat/completions` with a user API key. Models need native tool calls; image tasks also need vision. Context, images and tool results pass through the service; HOM executes in the current Houdini session on its UI thread. No workstation listener or MCP server is required.

## Scene tools

| Tool | Purpose |
| --- | --- |
| `houlm_session_info`, `houlm_get_selection` | Session metadata and selected paths |
| `houlm_inspect_node`, `houlm_list_children` | Authored parameters, wiring, immediate children and existing diagnostics |
| `houlm_list_node_types` | Installed node-type discovery |
| `houlm_parameter_schema`, `houlm_node_type_schema` | Instance/static parameter definitions |
| `houlm_hom_help` | Installed API signatures and docstrings |
| `houlm_propose_python` | General Python/HOM execution after review or Auto-run |
| `houlm_propose_edits` | Typed create/parameter/connect/flag/position operations |

Routine reads do not force cooks. Broader Python actions and [native viewport capture](visual-reference.md) may evaluate the scene. Typed edits preserve existing expressions/animation; use general Python for requested ramp, multiparm or channel work.

Complete tool calls are validated before dispatch; commands and results remain inspectable. See [execution and recovery](agent-execution.md) or [response diagnostics](tool-stream-troubleshooting.md).
