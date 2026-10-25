# Candidate security review

[中文](../安全审查_20261025.md) | [English](security-review.md)

Scope/date: 2026-10-25, candidate 0.5 source, pinned dependencies, bootstrap state paths, CLI/JSON/MCP configuration, material XML, distribution membership and public Git history. No user-device finance session, verification code, real submission or approval was exercised. This is not a live acceptance receipt or a guarantee of no defects.

| Finding | Candidate remediation |
| --- | --- |
| Old portable Python 3.12.13 actually used Expat 2.6.3 | Pin uv 0.13.5 / Python 3.12.15; new macOS runtime readback reports Expat 2.9.0. XML entry points reject parsers below 2.8.5 |
| Pillow 12.2.0 matched upstream known advisories | Pin 13.0.0 and recheck image/PDF/OCR and cross-platform installation |
| OpenTelemetry API 1.45.0 matched CVE-2026-114603 | Pin 1.45.1; default stdio does not expose an HTTP service, but the dependency is still patched |
| Multiple Office/OFD XML readers; ASCII-only declaration checks could miss UTF-16 | Shared safe_xml/defusedxml boundary, entities/external access rejected, byte/depth/element limits, bounded ZIP reads |
| A verified vendor source was reread by a loader that could accept neighboring pyc | Compile/execute the exact verified bytes; regression uses equal-size/timestamp alternative bytecode |
| Bootstrap could follow runtime links before Python-stage validation | Check before download/write/--check execution; Windows permits only the frozen minor-version Python alias to its exact sibling full-version real directory; all other reparse points are rejected and aliases are not traversed |

Previous fixes for authorization-revocation CAS, same-path bridge state protection and cross-user setup-task cleanup retain their regression tests. Candidate configuration and synthetic receipts never become live finance evidence.

The dependency check queries known advisory data for the complete locks; consult CI for the exact commit. Advisory rows contain duplicate IDs/aliases, so raw row counts are not counts of distinct flaws. Candidate fixes do not update old installations or existing Release assets.

Bandit is a locator for review, not a vulnerability verdict. XML entry points now share the restricted parser. SQL identifiers/fragments come from fixed sets and user values are bound separately. Helper execution uses fixed SHA-verified bytes. The Mac authentication socket uses mkdtemp and 0700/0600 permissions. Ordinary subprocess calls, synthetic assertions and empty password defaults are not globally suppressed to manufacture a zero-warning scan.

Public source/history are scanned with gitleaks redaction. The extension identity public key has a narrowly scoped exact-file/exact-value exception. Packaging uses an explicit allowlist and excludes cases, private policies, runtime state, account sessions and credentials.

The local operator, installed code/runtime and configuration are trusted; materials may be untrusted. A matching hash does not establish trust in arbitrary operator-selected code. This is not a sandbox against a compromised local administrator or a qualified multi-user network service.

Preview and execution remain separate, with boolean execution flags and source/version checks. Unknown outcomes retain original work. Verification codes stay manual and window display is explicit. Windows live setup/login/code continuation/approval/native exports remain deferred; the fixes are included in the 0.5.0-beta.1 prerelease. Prerelease publication does not change the untested live-finance status.

Sources: [pip-audit](https://github.com/pypa/pip-audit), [Python XML security](https://docs.python.org/3/library/xml.html#xml-vulnerabilities), [Pillow 13.0](https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html), [OpenTelemetry 1.45.1](https://github.com/open-telemetry/opentelemetry-python/releases/tag/v1.45.1).
