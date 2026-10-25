# Security and privacy

[English](SECURITY.en.md) | [中文](SECURITY.md)

Use [private vulnerability reporting](https://github.com/Rtiming/ustc-reimbursement/security/advisories/new) for credential disclosure, unauthorized reads/writes, duplicate submission or private-data leakage. Do not post real invoices, identities, accounts, cookies, verification codes or complete runtime logs in public issues.

Provide the version, OS, synthetic reproduction, expected/actual behavior and sanitized evidence. Do not repeat real approval, deletion or payment to demonstrate a defect. Revoke exposed credentials at their service before treating removal from Git as a remedy.

The trust boundary is a local, trusted operator and installed code/runtime/configuration, processing potentially untrusted materials. This is not a hardened multi-user network service or a sandbox against a compromised local administrator. stdio MCP has no public listening port. Material roots, fixed operations, source hashes, owner permissions and original-task readback constrain the normal interface; they do not make arbitrary operator-selected code trustworthy.

Unknown finance actions retain their original tasks. Verification codes remain manual. OCR and planning output remain candidates. Browser setup, login, saved values, approval and payment are separate acceptance states.

This prerelease has a dated [security review record](docs/en/security-review.md). Dependency checks report known advisories at query time, not all possible flaws. Static scan findings need manual interpretation. The previously published 0.4 package is unchanged by fixes in 0.5.0-beta.1; do not assume an old installation has been patched.

Windows live finance acceptance is currently deferred. A CI pass or a security review does not certify that deferred workflow. Network service wrappers require separate authentication, isolation, audit, deployment validation and applicable license compliance.
