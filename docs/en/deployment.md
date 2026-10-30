# Deployment

[中文](../部署指南.md) | [English](deployment.md)

Use a release attachment for routine installation and a Git checkout for development. This is a local CLI and stdio MCP service: no public HTTP endpoint, Docker, or maintainer account is required.

| Platform | Bootstrap targets | System prerequisites |
| --- | --- | --- |
| macOS | arm64, x86_64 | sh, curl, unzip, shasum |
| Windows | x64, ARM64 | Windows PowerShell, .NET ZIP/cryptography support, permitted script execution |
| Linux | x86_64, aarch64 | sh, curl, unzip, awk, SHA256 utility; glibc distribution |

A bootstrap target is not proof that every architecture has been tested. See [validation](validation.md). Windows ARM64, macOS Intel, Linux ARM64, and Alpine/musl are not covered by the prerelease's current CI matrix. Package wheel availability can further restrict optional OCR.

On a minimal Debian/Ubuntu system, ask its administrator to provide `curl`, `unzip`, `ca-certificates`, and, for OpenCV/OCR, `libgl1` and the distribution's glib runtime package (`libglib2.0-0` or its `t64` replacement). The installer does not bypass operating-system policy.

## Fresh installation

1. Download the attached installation ZIP and `SHA256SUMS`. Use `shasum -a 256`, `sha256sum`, or PowerShell `Get-FileHash -Algorithm SHA256` to compare the download.
2. Extract to a writable, persistent directory. Quote paths containing spaces; Unicode paths are covered by the isolated CI workflow.
3. Run `sh install.sh` on macOS/Linux or `install.cmd` on Windows. PowerShell requires `./install.cmd`.
4. Read the returned `ok` and report path. `.local/self-check-current.json` points to JSON and HTML reports.
5. Configure your own data directory, profile, rules, and AI client. Browser setup is a separate step.

```sh
sh install.sh --data-dir "/absolute/path/materials" --profile-id project_alpha --title "Project Alpha" --mode student_activity
```

```powershell
./install.cmd --data-dir "D:/Reimbursement/materials" --profile-id project_alpha --title "Project Alpha" --mode student_activity
```

The launcher verifies uv 0.13.5 and obtains pinned Python 3.12.15, then installs locked packages under `.local/`. It does not change global Python, PATH, Python registry entries, or global AI configuration. Access to PyPI distribution hosts and the managed Python download host is required initially. Do not disable TLS or checksum verification to work around network failures.

User pip/uv/Python configuration is isolated. Organization-specific mirrors and custom certificates are not automatically imported; configure and validate such deployments explicitly. The project does not copy another person's environment or secrets.

## Optional OCR and offline checks

`sh install.sh --with-ocr` (Windows: `install.cmd --with-ocr`) explicitly downloads CPU dependencies/models and tests a synthetic image. Default installation does not download models or upload documents.

`sh install.sh --check` (Windows: `install.cmd --check`) checks an existing installation without installing dependencies or changing policies. It exercises CLI, stdio MCP, and synthetic intake. Optional-provider absence is separate from required checks. Failure logs are in the same report directory; retry normal installation only after resolving the cause, preserving existing user data.

## Git checkout

```sh
git clone https://github.com/Rtiming/ustc-reimbursement.git
cd ustc-reimbursement
sh install.sh
```

Use `install.cmd` for the last command on Windows. A checkout is verified as `current_git_checkout`; a release archive is checked against its frozen manifest as `release_manifest`. A GitHub source ZIP without `.git` and without the release manifest is neither route: use the installation attachment or build a package yourself.

## Upgrade and remove

Finish or resolve existing work first. Install an upgrade into a new directory and preserve the old version and receipts. Reuse an explicitly authorized data directory; move case registrations and completed records only through their documented case handoff. Do not copy a venv, browser database, or the entire `.local` tree. An unknown finance write must be resolved in its original task before starting replacement work.

Regenerate AI configuration on the receiver. A bridge migration must pass its own preview, owner, lock, and pending-request checks. Do not hand-edit registry keys to force it. Before deleting an installation, retain originals, policies, cases, and receipts and remove any native-host registration through the corresponding controlled maintenance route.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Missing system utility | Install the prerequisite; do not skip checksums |
| PowerShell execution blocked | Follow organization policy; the launcher does not change ExecutionPolicy |
| Download/TLS failure | Check network, clock, and certificates; preserve logs |
| Missing libGL/glib | Install the Linux system libraries used by OpenCV |
| Manifest mismatch | Restore the verified release; make code edits in a checkout |
| Generated entry differs from source | Follow CONTRIBUTING to synchronize declared aliases |
| Rules/provider missing | Configure it; absence is not a successful business check |
| Bridge unavailable | Follow the [batch guide](batch-workflow.md); installation success does not prove login |

The deployment pattern follows [uv's managed-Python guidance](https://docs.astral.sh/uv/guides/install-python/). Containerized or network-hosted finance automation is not a qualified deployment mode for this project.

## Deployment files and a read-only plan

Copy `distribution/deployment.example.json` to a private file. Optional `data_dir` must be an absolute path on this machine. Only schema, data_dir, profile_id, title, mode and with_ocr are supported. Conflicting CLI/file values fail explicitly. Credentials and commands are not accepted.

```sh
sh install.sh --config /absolute/path/deployment.local.json --plan
sh install.sh --config /absolute/path/deployment.local.json
sh install.sh --config /absolute/path/deployment.local.json --check
```

Use `install.cmd` on Windows. Planning requires an installed runtime or Python3.11+; it never downloads one. A plan does not create state or run probes. `--check` works in any argument position without installing. A successful installation records version, source-tree SHA and its acceptance receipt in deployed_source inside `.local/installation.json`. Failed installation does not advance that identity.

## Updating a user installation

```sh
sh update.sh
sh update.sh --install
```

Windows: `update.cmd` and `update.cmd --install`. The first command only checks this repository's public releases. The second verifies SHA256SUMS, the GitHub asset digest when present, all manifest hashes and exact ZIP membership, then installs and checks a separate version directory. Prerelease users follow the preview channel by default; stable users follow stable releases. Override with `--channel stable` or `--channel preview`. Use `--version vVERSION` for a specific release, `--destination ABSOLUTE_PATH` for the new directory or `--download` to only prepare verified files.

The old installation and task evidence remain available. The bound material directory and profile are reused, and explicit local OCR remains enabled when previously configured. A verified destination can be resumed; unrelated existing directories are never overwritten. Only a successful fresh installation reports installed and a new MCP configuration. Merge that fragment and restart that service. Failed installation leaves logs under the new directory's `.local/update-install.log`.

This is side-by-side preparation, not an in-place update or automatic activation. Cases, task history, finance login and private integrations are not migrated. Continue old cases and unknown actions from the old directory; use the new directory for new work. Retain the old directory if it contains the material root. Roll back by using its original entry point. Versions without the updater require a first manual verified download of this release.

Synthetic self-check workspaces are retained alongside their reports for inspection. They are not immediately deleted while Windows may still hold log handles. Functional check failures still fail the installation.
