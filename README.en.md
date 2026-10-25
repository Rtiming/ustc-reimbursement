# USTC Reimbursement Toolkit

[English](README.en.md) | [中文](README.md)

A local reimbursement assistant for people and AI agents: document intake and archiving, PDF/OCR, sourced policy profiles, CLI/JSON, and stdio MCP. The browser adapter targets the University of Science and Technology of China (USTC). This is an independent project, not an official university service.

This is **0.5.0-beta.1**, a prerelease for isolated installation, document handling and interface evaluation. See the [bilingual release notes](docs/releases/v0.5.0-beta.1.md). **The real Windows finance workflow has not been tested.** Live approval, portal exports and the five-minute batch target are not certified. Existing 0.4 installations do not receive these security fixes automatically.

## Install

Published packages are under [Releases](https://github.com/Rtiming/ustc-reimbursement/releases). Choose an attached `ustc-reimbursement-*.zip`, verify `SHA256SUMS`, and extract into a writable directory. GitHub's automatically generated “Source code” ZIP is a developer snapshot, not the verified installation archive.

| System | Install in the extracted directory | Check an existing installation |
| --- | --- | --- |
| Windows | `install.cmd` (PowerShell: `./install.cmd`) | `install.cmd --check` |
| macOS | `sh install.sh` or the included `.command` launcher | `sh install.sh --check` |
| Linux | `sh install.sh` | `sh install.sh --check` |

The installer downloads a pinned, project-local Python and locked dependencies. It does not change global Python, PATH, or AI client configuration. The first installation needs internet access. OCR models are optional: add `--with-ocr` explicitly.

```sh
sh install.sh --data-dir "/absolute/path/reimbursement data" --profile-id project_alpha --title "Project Alpha" --mode student_activity
```

On Windows, use `install.cmd` and a Windows absolute data path. Configuration starts with empty, unconfirmed rules. See [deployment](docs/en/deployment.md), [configuration](docs/en/configuration.md), and the [user guide](docs/en/user-guide.md).

## Connect an AI agent

Merge the generated `.local/client-configs/mcp.json` service into a compatible stdio MCP client. Preserve its other services. Clients with command execution can read [AGENTS.en.md](AGENTS.en.md) and use the same JSON contract:

```json
{"method":"capabilities","params":{"intent":"materials"}}
```

Save the request and run `sh tools/reimbursement.sh call --input /absolute/path/request.json`. On Windows use `tools\reimbursement.cmd`. Read English task guidance with:

```json
{"method":"guide","params":{"topic":"quickstart","language":"en"}}
```

`mcp/tool_catalog.json` is the executable parameter and CLI/MCP route reference. Runtime messages are currently mainly Chinese; operation names, error codes, and JSON fields are stable identifiers. English documentation does not translate or alter business data.

## Capabilities and limits

| Capability | Requirements |
| --- | --- |
| Intake, policies, offline planning, PDF | Local installation, authorized source directory, current organization rules |
| Local OCR | Explicit `--with-ocr`; recognized fields remain candidates |
| Remote OCR / ledger | A separately configured provider; the maintainer's infrastructure is not bundled |
| USTC finance browser | Local Edge setup, the user's own account, case-specific authorization and readback |
| Registered batch workflow | Shared macOS/Windows test implementation; real Windows finance remains untested |
| Linux finance browser | Unsupported |
| Mobile capture / authored spreadsheets | Optional external adapters and runtimes |

Detail PDFs and the portal's native list export are separate actions. The owner enters the verification code. Installation checks use synthetic data and do not approve real documents. See [batch workflow](docs/en/batch-workflow.md) and [validation scope](docs/en/validation.md).

## Documentation and contributions

- [Configuration](docs/en/configuration.md) · [Interfaces](docs/en/interfaces.md)
- [Rules and planning](docs/en/rules-and-planning.md) · [Materials and OCR](docs/en/materials-and-ocr.md)
- [Contributing](CONTRIBUTING.en.md) · [Security](SECURITY.en.md) · [Release maintenance](docs/en/maintenance.md)

Only public source, blank defaults, and synthetic tests belong in this repository. Keep real cases, private policies, accounts, cookies, verification codes, and runtime logs local.

Original code and documentation are licensed under [AGPL-3.0-or-later](LICENSE). Third-party packages and models retain their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.en.md). The license does not grant access to university systems or establish reimbursement policy.
