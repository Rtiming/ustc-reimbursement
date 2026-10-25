# Registered finance batches

[中文](../整批流程与登记.md) | [English](batch-workflow.md)

The 0.5 candidate shares native workpage, identity and recovery contracts across macOS and Windows. Windows live finance acceptance is deferred. Linux supports offline work only. Do not use synthetic checks as evidence of live approval.

## Browser setup

Install the package first. Keep the current user's existing Edge open on a logged-in Windows desktop. Preview:

```json
{"method":"bridge","params":{"action":"install"}}
```

Review the returned registration paths and SHA. Execute with a stable request ID and that exact SHA:

```json
{"method":"bridge","params":{"action":"install","execute":true,"request_id":"edge-setup-01","expected_registration_sha256":"REPLACE_WITH_PREVIEW_SHA"}}
```

Poll `bridge(action="installation-result", request_id="edge-setup-01")`. The fixed installer runs through the current user's existing interactive token; it stores no password and does not elevate privileges. An SSH Session 0 desktop is not treated as the user's desktop. Result polling does not rerun installation and may remove only the completed, identity-matching one-time setup task, preserving its receipt.

Busy/current native hosts, pending requests, changed registrations, wrong users and unexpected program identities stop setup. A controlled migration saves previous registration metadata and never copies browser profiles or deletes business records. Browser policy is not bypassed. Verify `bridge status` and `bridge inventory`; connection is separate from login and business acceptance.

Log in personally. New registrations use `session_only`; they do not need the author's credential vault. The expected finance user must match the live account.

## Before registration

Use `new_case`, intake, profile selection and `prepare_batch_file`. Use the existing controlled upload/select/create/readback interfaces to obtain real serials and invoice bindings. Registration does not manufacture them.

Create a separate batch navigation case without a serial. Each document case must correspond to one reviewed, split form and one normalized material group. Current preparation supports confirmed no-contract/self-printing options. Unknown values are not defaults. Each plan must bind its case, current profile version, normalized source SHA, amount, summary and confirmed export date/sequence. Department approvers must have explicit project/ID/name/source bindings.

## Preview and save

This is a template, not authorization or real evidence. Replace every placeholder and path with the receiver's confirmed data.

```json
{
  "method":"register_workflow",
  "params":{
    "case_id":"my_batch",
    "output_dir":"D:/Reimbursement/materials/registration-v1",
    "request":{
      "schema":"ustc-workflow-registration-v1",
      "scenario":"my_project",
      "expected_userid":"REPLACE_WITH_ACCOUNT_ID",
      "source":"Location of the current case-scope and authorization confirmation",
      "authorize_prepare":false,
      "authorize_approve":false,
      "show_verification_interface":true,
      "department_approvers":[],
      "documents":[{"case_id":"actual_document","plan_path":"D:/Reimbursement/materials/document-plan.json"}],
      "delivery":{"export_root":"D:/Reimbursement/materials/system-exports","download_directory":"D:/Actual-Edge-Downloads","format":"EXCEL","filename_separator":"_"}
    }
  }
}
```

On macOS/Linux replace paths with local absolute paths. After reviewing the preview, add `execute:true` and its `expected_preview_sha256` to params. The tool creates immutable index, authorization, export and definition files, and binds the local navigation case. Existing versions and colliding export files are not overwritten.

Preparation and final owner agreement are separately authorized. Set each flag only for the user's current exact scope; `approval_opinion` stays the portal's literal `同意`, not an English translation. Displaying the verification window is opt-in; absent `show_verification_interface` means false.

Without native preparation evidence, registration does not create an executable approval manifest. After every selected document has verified preparation readback, `ready` can generate a new bound approval-input version under the existing authorization. Alternatively provide real `case_id/job_id/artifact_id/sha256` references as `preparation_receipt`; they are validated before registration. A plan or mock receipt cannot stand in for native evidence.

## Stages and recovery

```json
{"method":"workflow","params":{"case_id":"my_batch","phase":"status"}}
```

| Phase | Result | Required evidence |
| --- | --- | --- |
| `fill` | Fill and verify an existing draft | Current plan, originals/selection, fields and preparation authorization |
| `ready` | Prepare for owner review | Account, serial, approvers and independent native readback |
| `approve` | Owner agreement and post-code continuation | Native preparation receipts and exact approval authorization |
| `export-system` | Original detail PDF archive | Confirmed naming inputs and output root |
| `export-list` | Native list file | Exact serial set, format and actual download directory |
| `finish` | Approval followed by both exports | All three input/preflight checks pass |

Preview with `execute:false`. Use boolean true only for the authorized execution; strings are rejected. The MCP method is `reimbursement_workflow`. For longer work use the same CLI contract, such as `tools\reimbursement.cmd workflow --case-id my_batch --phase ready --execute`, retaining the process and receipts.

At `verification_required`, the owner enters the code in the bound official page. Explicit window-display consent may bring that page forward. Continue the original batch/approve phase afterward. The tool first reads native state; if the callback already approved the document, it records the observed result instead of clicking agreement again.

Changed accounts, namespaces, files, definitions or authorization stop old work. Revoked authorization cannot be restored by a late preparation result. Unknown actions retain their original job and page context. A missing page is not replaced as if it were the original.

Detail PDFs and list exports stay separate. Rename/archive the mapped originals; do not parse amounts or page layout again merely to archive them. New `finish` registrations require both export plans. Neither output proves supervisor approval, official printing or payment.

Real acceptance must later cover login, owned workpages, owner-entered verification, per-document readback, both actual exports and recovery. Already-completed bills must not be approved again for testing.
