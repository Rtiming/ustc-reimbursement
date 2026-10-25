# CLI, JSON and MCP

[中文字段参考](../参数与字段目录.md) · [English user guide](user-guide.md)

`assistant.py` and stdio MCP invoke the same `AssistantService`. `tools/assistant_contract.py` owns the fixed methods, enums and route mapping. [mcp/tool_catalog.json](../../mcp/tool_catalog.json) is generated from the actual SDK tool definitions and is the parameter reference; this page does not duplicate its schema.

| Task | JSON method / route | MCP |
| --- | --- | --- |
| Discover | `capabilities` | `reimbursement_capabilities` |
| Read guidance | `guide` with `topic`, `language` | `reimbursement_guide` |
| Cases | `cases`, `overview`, `new_case`, `update_case` | Same names prefixed with `reimbursement_` |
| Policies | `standards_read/init/change/select/add_profile` | Corresponding catalog entries |
| Material/OCR | `material_intake`, `material_ocr`, `ocr_status`, `material_ocr_resume` | Corresponding catalog entries |
| Browser setup | `bridge` | `reimbursement_bridge` |
| Batch registration | `register_workflow` | `reimbursement_register_workflow` |
| Registered phases | `workflow` | `reimbursement_workflow` |
| Job operations | `enqueue` with a fixed `operation` | Catalog `json_routes` specifies each route |
| Read original work | `result` | `reimbursement_result` |

Do not infer every JSON method by removing an MCP prefix. For example, financial job tools may map to `enqueue`, not a direct method. Unknown methods, fields or operations must be corrected rather than routed to arbitrary code.

Request files use UTF-8 JSON. Use booleans, not strings such as `"false"`. Planning and execution are separate intents. Version/SHA conflicts require a fresh read and review; an unknown outcome requires the original job and receipt, not a new request ID.

```json
{"method":"guide","params":{"topic":"batch-workflow","language":"en"}}
```

`language` is `zh` or `en`. The default remains Chinese for compatibility. It affects returned documentation only. All topic keys exist in both language maps; executable parameters, account identifiers, the portal opinion literal `同意`, currency formats and policy statements remain unchanged.

Typical states include queued/running, completed, verification_required, blocked and unknown. A process exit is not a finance result. Error codes and structured details are authoritative; human-readable runtime messages currently remain mainly Chinese.

MCP uses standard input/output only. The package does not expose an unauthenticated HTTP endpoint. Its client configuration is generated per installation, with exact local command, arguments and authorized material roots. Keep logs and generated client fragments out of public commits.
