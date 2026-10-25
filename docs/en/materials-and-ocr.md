# Materials, OCR and optional tools

[中文收件](../材料归档与归一化入口.md) · [中文OCR](../OCR统一流程.md)

Start with `capabilities(intent="materials")` and select an explicitly authorized data root. Create a local case before intake. `material_intake` uses schema `reimbursement.material-intake.v1`: the case ID, selected source IDs/absolute paths and a new output directory are explicit. The business request cannot select an arbitrary helper program.

Preview first. Execution requires a stable request ID. The tool freezes source selection, archives original bytes, verifies hashes and produces an inventory, group candidates, OCR candidates and issues. Output directories are not overwritten. Repeating the same request returns the original job; changing its intent under the same ID is rejected.

The archive adapter verifies its fixed helper bytes and executes those exact bytes, rather than accepting a neighboring pyc or rereading changed source. Windows reads use the existing native file-identity and read-lock boundary. Runtime configuration is private and trusted; a SHA identifies approved bytes, not the trustworthiness of an arbitrary operator-selected program.

Groups are explicit or unresolved. The tool does not guess a project, person, fee or claim amount from a filename. Archive members retain their source chain. Unsupported, unreadable or oversized materials remain issues; a successful archive is not proof that every required document exists.

## OCR

1. Call `ocr_status` to inspect the configured provider.
2. Use `material_ocr` with a completed intake job in the same case, optionally selecting candidate IDs and rotations.
3. Poll the original `result`. For a remote run, use `material_ocr_resume` with that original job/run.
4. Read text evidence and the enriched inventory. Source/page/SHA links remain explicit; extracted invoice fields remain candidates.

Native text is preferred and normally not re-recognized. Local CPU OCR is opt-in through `--with-ocr`; its model hashes are checked, with a bounded local page/thread budget. Remote OCR uses an explicitly configured provider. The package contains no maintainer server credentials and does not automatically upload documents elsewhere. Changed sources, models or runtime fingerprints stop reuse of the old result.

Recognition can misread numbers. It never confirms a payee, invoice number, amount, tax validation, approval or payment. Existing completed outputs may be reused only with matching source and artifact hashes.

## Material and print outputs

List people/proposed payees first, then expense/material details, then physical originals. Follow the selected evidence policy; attach available genuine payment records, flag missing items, and do not substitute an order, bill summary or chat for an actual payment. A shared payment record is attached once with its declared associations.

Electronic packets are continuous A4 single-sided PDFs, with one invoice or material page per sheet and per-item orientation. Physical originals are listed, not duplicated as scans or blank placeholders. Additional headings and footers default off. Confirm the source list and order before execution; a generated attachment packet is not a native finance PDF.

Untrusted Office/OFD XML goes through the shared restricted parser and bounded ZIP-member reader. It rejects entity declarations and external access, including encoded declarations. See [configuration limits](configuration.md); split oversized inputs instead of suppressing a failure.

## Optional components

- Official activity forms: acquire the current approved master yourself. The package includes a fixed-form adapter, not university binaries, signatures or stamps. Inspect `python reimburse.py activity --help`; an arbitrary Word document is not supported merely by renaming it.
- Ledger: expenses, persons, invoices, documents and relations stay separate. Query/trace operations use registered snapshots and fixed SQL plans with bound values. Remote bulk-ledger services are separately configured.
- Spreadsheet authoring and mobile capture: these require their own optional runtimes/adapters. Plain JSON/CSV is not an authored workbook, and a plan is not a successful device capture.

For intake/printing/ledger-specific field details, use the canonical schema and the linked Chinese technical references. No optional component may silently substitute a different cloud service or acquire additional accounts.

XML requires Expat 2.8.5 or newer. Python 3.11+ syntax compatibility does not certify an old interpreter; prefer the pinned Python 3.12.15 runtime.
