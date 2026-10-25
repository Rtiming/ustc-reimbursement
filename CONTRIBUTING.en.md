# Contributing

[English](CONTRIBUTING.en.md) | [中文](CONTRIBUTING.md)

Use [the public repository](https://github.com/Rtiming/ustc-reimbursement) for issues, pull requests and releases. Submit public implementations, blank defaults and synthetic tests only. Do not include real invoices, private rules, account configuration, browser state or raw logs.

After installation, use `.local/mcp-venv/bin/python` (Windows: `.local/mcp-venv/Scripts/python.exe`):

```sh
.local/mcp-venv/bin/python tools/check_public_docs.py
.local/mcp-venv/bin/python -m unittest discover -s tests -p 'test_*.py'
.local/mcp-venv/bin/python tools/distribution_smoke.py --mcp --materials --output .local/dev-smoke.json
```

OCR changes require explicit OCR installation and a smoke run with `--ocr`. Relevant browser contracts are Node tests, not live finance calls. CI must not log into the school, acquire real invoices, or send approval operations.

`assistant_contract.py` owns fixed interface names; `workspace_config.py` owns local binding; `reimbursement_standards.py` owns private rules. XML readers share `safe_xml.py`. Do not duplicate these contracts in translated documents or new services.

Add public files to `distribution/source-files.json`. Edit generated root documents in `distribution/`, then preview and run `tools/prepare_public_checkout.py --execute`. Chinese and English guidance must stay aligned; language selection cannot change authorization or data.

Inspect the actual diff and stage selected files. A PR should describe the trigger, resulting behavior, checks and untested platforms. Do not weaken failures, clear unknown operations or replace evidence with a synthetic success. Review and [security procedures](SECURITY.en.md) retain their own scope.

Contributions use AGPL-3.0-or-later. Preserve original attribution and licensing for third-party material. Dependency changes require matching locks, notices and affected-platform validation. See [maintenance](docs/en/maintenance.md).
