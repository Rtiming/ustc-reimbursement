# Configuration

[中文](../配置说明.md) | [English](configuration.md)

This page describes the 0.5 candidate. Windows live finance acceptance is deferred. Language selection changes guidance only, not permissions, business fields, or rules.

| Setting | Interface | Default / boundary |
| --- | --- | --- |
| Data root | installer `--data-dir` | `materials` under the tool; choose a dedicated directory, not an entire home or disk |
| Profile ID/title | `--profile-id`, `--title` | `my_project`; initially empty and unconfirmed |
| Material mode | `--mode` | `student_activity`; also `research_purchase`, `travel`; not an eligibility decision |
| OCR | `--with-ocr` | Off by default; configure your own local or remote provider explicitly |
| Guidance language | `guide(topic, language)` | `zh` or `en`, default `zh`; case data is unchanged |
| Browser | `bridge` preview then execute | The current user's existing Edge and the fixed project extension |
| Batch | `register_workflow` preview then execute | Exact cases, account, plans, authorization and preview SHA |

Use the commands in [deployment](deployment.md). Re-running installation preserves the binding and existing rules. A missing previously bound rule library must be restored rather than replaced with empty rules.

| Location | Owner | Sharing rule |
| --- | --- | --- |
| `distribution/data/*`, requirement locks | Public source / installer | Distributable neutral defaults |
| `.local/installation.json` | Local installer | Private receiver-specific path binding |
| `.local/client-configs/` | setup | Regenerate on every receiver; do not copy absolute author paths |
| Data root / `规范库` | standards API | Private sourced policies |
| `cases/` | case API and user confirmation | Private business registrations |
| `.local/edge-bridge/` | local bridge | Private intent/response evidence; never clear pending work blindly |
| `.local/ocr-provider.json` | OCR setup | Private provider and machine configuration |
| `.local/self-check-current.json` | self-check | Local JSON/HTML report pointers, potentially containing personal paths |

Do not hand-edit installation metadata to broaden authorization. CLI and MCP share the installation binding; explicit material-root overrides must remain within the user's current authorization. Keep material output separate from code. The portable launcher rejects escaping runtime links. On Windows, only the frozen Python minor-version alias may point to its exact sibling full-version real directory; other reparse points, broken/chained links, UNC and device-path targets are rejected. The alias itself is never traversed; the real directory is checked normally. A manually created venv linked to external Python is a development environment, not a portable installation; preserve it and use a new directory for the portable package.

Merge only this service from `.local/client-configs/mcp.json` into a compatible stdio client. For clients requiring TOML, use the generated fragment and their official import procedure. No global AI settings are rewritten. Runtime messages are mainly Chinese; stable method names, JSON keys and error codes are shared across languages.

```json
{"method":"guide","params":{"topic":"configuration","language":"en"}}
```

For finance, select `cases → overview`, then follow the [batch guide](batch-workflow.md). Preparation and owner approval need separate authorization. `show_verification_interface` defaults to false; explicit true allows the bound code-entry window to be shown. The owner enters the code on the official site.

Configure the detail-PDF root, native list format and actual Edge download directory separately. Dates, sequence numbers and summaries must come from confirmed plans. Registration does not resolve unknown facts, missing native evidence, changed SHAs or a user's hold instruction.

Office/OFD XML rejects entities and external resources. Limits are 64 MiB per XML input, depth 128, 500000 elements, and 10000 archive members / 512 MiB expanded archive size. Oversized sources must be split, not silently truncated. Ordinary DOCTYPE declarations from the project's Poppler-generated XHTML are permitted; entities and external-resource access remain forbidden.

See [security](../../SECURITY.en.md). Configuration checks and dependency scans have explicit scopes and dates; they are not proof of every device, website revision, or finance action.

XML requires Expat 2.8.5 or newer. Python 3.11+ syntax compatibility does not certify an old interpreter; prefer the pinned Python 3.12.15 runtime.

The JSON, TOML and YAML client fragments share one binding. Self-check verifies every generated fragment against the current tool path, data roots and arguments; a missing or changed fragment prevents a ready result. The receiving client's global configuration is outside this local check.
