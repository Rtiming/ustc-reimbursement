# Development and release maintenance

[中文](../发布维护.md) | [Contributing](../../CONTRIBUTING.en.md)

Public collaboration and releases belong to [this repository](https://github.com/Rtiming/ustc-reimbursement). Private operational histories, cases, policies and credentials must not be pushed wholesale into it. The initial public history was constructed from an explicit source allowlist.

`distribution/data/distribution.json` owns the candidate version. `distribution/source-files.json` selects the exact distributable files. `tools/build_distribution.py` declares generated aliases; edit their source, then run `tools/prepare_public_checkout.py --execute` in a public checkout. `MANIFEST.json` belongs to an installation ZIP and is not committed to the source branch.

Chinese and English topic maps are owned by `tools/assistant_contract.py`. Each map must cover the same topic keys. Keep the same configuration values, authority, default behavior and validation limits in both languages. Update linked task pages rather than creating a second business implementation.

Before packaging, run the documentation/configuration check, applicable regression and synthetic protocol tests. Build into a new absolute directory with `tools/build_distribution.py --output-dir ... --execute`, then validate the exact extracted package. Keep receipts separate from source.

Before public delivery, inspect the actual staged files and archive members; scan source and public history for secrets with redaction; review dependencies and preserve third-party notices. A package does not include downloaded runtimes/models or private state. Do not upload local build receipts containing user paths; publish a sanitized acceptance summary and checksums instead.

Use immutable tags and release assets. Read back the remote commit/tag and asset hash. Existing release bytes and prior failure receipts are retained. Public CI uses synthetic inputs and read-only repository permissions, without school accounts or secrets.

The 0.5 Windows candidate remains a draft until live acceptance is explicitly resumed and completed. A documentation or security fix in that branch is not a new public release and does not change an already-installed 0.4 package.
