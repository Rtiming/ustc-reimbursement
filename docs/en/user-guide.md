# User guide

[中文](../用户指南.md) | [English](user-guide.md)

Start with an offline exercise. The tool assists an existing finance system; it does not replace institutional approval or payment.

## Install and discover

Follow [deployment](deployment.md) and [configuration](configuration.md). Originals and private rules belong in your explicitly authorized data root. Case registrations and runtime receipts are separate from public code. Never share an installation by zipping its entire used directory; rebuild from the source allowlist.

Run the platform installer, inspect its HTML report, and configure your AI client from the locally generated service entry. A command-only agent can use:

```json
{"method":"capabilities","params":{"intent":"materials"}}
```

Save the request as UTF-8 JSON and invoke `sh tools/reimbursement.sh call --input /absolute/path/request.json` (Windows: `tools\reimbursement.cmd`). Start with `materials`, `rules` or `ocr` for offline discovery; these intents do not probe browser registration or a user's browser state. Finance/export discovery checks local setup but does not prove login.

Read English guidance with `guide(topic="quickstart", language="en")`. See [interfaces](interfaces.md) for method mapping and [AGENTS.en.md](../../AGENTS.en.md) for agent boundaries.

## Configure private rules and cases

Installation creates empty common/project profiles. Use `standards_read` to inspect them and the standards preview/execute interfaces to add sourced, scoped rules. Do not treat the author's examples or another case's limits, identity, funding or authorization as your rules. See [rules and planning](rules-and-planning.md).

Create a local case with `new_case`, then read `overview`. This does not create a finance-system document. Update allowed business fields through `update_case` with the returned revision. Serial numbers, selected invoice GUIDs and financial outcomes require their controlled evidence routes.

## Process materials

1. Use `material_intake` to archive original bytes and source hashes. Keep electronic originals, scans and physical originals distinct.
2. Review candidate fields against originals. Unknown names, identifiers, dates, funding and amounts remain unknown.
3. Use `ocr_status` and `material_ocr` only for selected pages without usable native text. Recognition remains candidate evidence.
4. Use `prepare_batch_file` with the selected profile, explicit case context, business date and normalized JSON. The tool compiles supported rules; the agent must not invent a second policy.
5. Read structural, amount, export and finance readiness separately. A configured profile or completed program is not proof that all evidence is complete.

The output order is people/proposed payments, material and expense details, then physical-original arrangements. Existing payment evidence is attached according to the selected policy, without blank placeholders or fabricated missing evidence. An order or chat is not automatically a payment record. One combined payment can support multiple invoices without being counted repeatedly.

Electronic attachments are continuous A4 single-sided pages, one invoice or material page per sheet, with orientation selected per item. Physical originals are listed rather than printed as scans or blank placeholders. Additional headings/footers are off by default. See [materials and OCR](materials-and-ocr.md).

## Finance and registered batches

Set up your own existing Edge, log in as yourself, and verify the intended account and document. Use [batch registration](batch-workflow.md) to bind reviewed per-document plans. A batch navigation case is separate from its actual finance documents.

Preview each stage. Execute only the authorized stage and exact case set. The owner enters verification codes; neither code content nor passwords belong in chat or request files. Save, owner agreement, supervisor approval, printing, original delivery and payment remain separate facts.

The detail PDF and the native list export have distinct plans and receipts. Once mapping is established, preserve and archive original bytes; check download completion, missing files and collisions. Do not replace a system PDF with a screenshot or reconstructed HTML.

## Failure and upgrade

Queued/running means unfinished. `completed` alone means a process ended, not that finance state was independently confirmed. Read the original job and its artifacts. Unknown writes cannot be retried with a new request ID, cleared pending record or replacement tab. Changed configuration, source hashes, identity or connection binding stops old work.

Upgrade in a new directory, retaining prior code and receipts. Do not migrate active browser sessions, credentials or Python environments. Optional remote-ledger, mobile and spreadsheet-authoring services are not bundled; report missing providers instead of silently substituting a new upload service.

Current automated evidence is described in [validation](validation.md). Windows live financial acceptance remains deferred, and there is no verified five-minute batch promise.
