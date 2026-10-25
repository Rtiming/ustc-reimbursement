# Rules and planning

[中文规则](../模式与场景.md) · [中文规划](../规范维护与批次规划.md)

Rules, case facts and device configuration have different owners. Common rules are reusable; a project profile holds real differences; a case holds its actual people, funding, materials and authorization. Device paths, login sessions and credentials remain local.

Initialize a new private library with `standards_init` in preview mode. Execute only after reviewing it. The generated `common` and project profiles are empty and collecting evidence; they do not confirm any policy. Existing libraries are not overwritten. Use `standards_add_profile` to append a project, with the registry SHA returned by its preview.

Use `standards_read` to inspect a profile and its SHA. `standards_change` previews a rule addition/revision/deactivation, then requires `expected_sha256` to save. A rule needs its type, status, applicability, effect and source/confirmation record. Source facts cannot be inferred from another person's historical case.

`extends` composes explicitly registered parent profiles. Legacy `inherits` is metadata, not automatic policy execution. Conflicts retain their sources and block until a sourced exception or explicit correction resolves them. Hierarchy is not an implicit override. Unsupported required rule types stay unresolved rather than silently disappearing.

`standards_select` receives the selected profile, case context, business date (`as_of`) and selection basis. Unknown, out-of-scope, expired and conflicting rules are distinct results. The selected rule version and SHA travel with the plan. `student_activity`, `research_purchase` and `travel` are material modes, not organization IDs or automatic authorization.

For large input, use `prepare_batch_file` rather than hand-transcribing invoices into a prompt. Its UTF-8 JSON input must follow `ustc-reimbursement-batch-input-v1` and reside inside an authorized material root. The tool reads frozen bytes, compiles supported selected rules, and can save a new plan file without overwriting its input.

A plan separates structural readiness, amounts, export naming and finance readiness. It retains source evidence and unknown fields. Confirmed dates and original folder summaries control export names; download date is not a substitute. A plan cannot invent a financial serial, invoice GUID, saved field, approval or payment.

For batch registration, each selected document case requires a reviewed plan containing exactly one split form and one material group. `register_workflow` creates the technical index and fingerprints; do not rename a multi-document planning JSON into a batch index. Follow [batch workflow](batch-workflow.md).

Amounts in ledger snapshots use integer currency minor units or null; finance form strings follow their existing contract. Preserve leading zeros in identifiers. Expense amount, invoice face value, payment evidence, proposed claim, approved amount and actual receipt of funds are separate facts. Candidate matching does not prove a person or payment relationship.

The complete machine-readable interface is [tool_catalog.json](../../mcp/tool_catalog.json). Existing legacy input formats and special policy converters remain documented in the Chinese technical reference; a language choice never changes their schema or business scope.
