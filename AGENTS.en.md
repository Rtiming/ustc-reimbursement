# Agent entry

[English](AGENTS.en.md) | [中文](AGENTS.md)

1. Read `docs/en/user-guide.md` and the README of the case explicitly selected by the user. Install with the platform launcher; use `--check` afterward. Resolve private paths from the receiver's `.local/installation.json`, never from the author's machine.
2. Start with `capabilities(intent)`. Follow `next_action` and `needed_inputs`. JSON methods and MCP names are mapped in `mcp/tool_catalog.json`; do not infer enqueue operations from tool names. `guide(topic, language="en")` returns English documentation.
3. Initialize an empty private rule library with `standards_init`; append profiles with `standards_add_profile`. Preview before saving and supply the returned SHA. `extends` composes explicit registered rules; unresolved conflicts do not become automatic overrides.
4. Select a real case through `cases → overview` before finance work. Verify the account, connection namespace, window, tab, serial, amount, and current authorization. Use only the fixed project adapters; do not substitute arbitrary URLs, JavaScript, coordinates, hidden controls, or direct finance HTTP calls.
5. Preview and execution are separate. Use stable request IDs. After a job is queued, read that original job. Unknown outcomes cannot be reset, retried under a new ID, or transferred to a replacement page. A changed configuration or authorization stops the old workflow.
6. Saved fields, owner approval, supervisor approval, official printing, original-document delivery, and payment are distinct states. The owner enters verification codes. Do not request them in chat, read them, or fill them.
7. Detail PDFs and native list exports remain separate. Once mapping is established, preserve original bytes and rename/archive; check completion, missing files, collisions, and recovery. Do not re-audit PDF text, amounts, page count, or layout just for archiving. Synthetic timings and cached receipts cannot prove a five-minute live batch.
8. Keep cases and device configuration private. Upgrade into a new directory and preserve old tasks/receipts. Do not copy login databases or overwrite unknown active work. Never publish real data in commits, issues, or test artifacts.

For scans: `ocr_status → material_ocr → result`; use `material_ocr_resume` for the original remote run. Existing native text is reused. Local models require explicit installation. OCR results do not confirm identity, amounts, or approval.

For registered batches, follow `docs/en/batch-workflow.md`. Registration does not create serial numbers or native preparation evidence. Missing credentials, remote providers, official form masters, or optional spreadsheet/mobile runtimes must be reported as missing, never silently replaced by unrelated services.

Chinese and English documents describe the same contract. Language selection affects guidance only; it does not change paths, permissions, profile IDs, finance fields, or request semantics. Current Windows live acceptance is deferred: do not promote offline or CI evidence to a live finance claim.
