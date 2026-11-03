"""实际派发前的传输约束；历史点击适配器不能成为HTTP失败后的回退。"""


def describe():
    from finance_native_stage import STAGES
    return {'native_stage_entry':'reimbursement_stage','native_stages':list(STAGES),
            'native_stage_validation':'offline_lifecycle_and_mac_live_basic_funding_payments_notes_saved_readback_passed',
            'native_stage_readback':'categories and implicit_single_funding use business/<stage>-prepare; other stages business/bill-read → record_result/ack-write',
            'native_noop_validation':'stage noop_only=true refuses required changes before pending; requires save_dispatched=false and independent readback',
            'native_stage_limits':{'funding':'daily_no_removal_allocation_follows_in_expenses',
                'expenses':'complete_budget_tree_no_invoice_level_reallocation',
                'payments':'personal_bank_transfer; existing_or_one_new_corporate_account; one_exact_new_or_existing_card_transaction; protect_other_payment_sections'},
            'native_upload_entry':'invoice/upload or upload-resume; upload-recognize continues only the original saved batch',
            'native_upload_limits':'unbound_daily_1_to_10_files_max5MiB_each; 1_to_100_exact_identities_per_file; medical_code8_number10_or_vat20; partial_recognition_blocks_whole_file_reupload',
            'native_upload_readback':'business/invoice-inventory → record_result/ack-write',
            'native_upload_validation':'mac_live_pdf_and_ofd_native_guarded_ocr_recognition_and_complete_authenticated_inventory_passed',
            'native_cleanup_entry':'invoice_retry/inspect → preview → cleanup|reidentify → inspect → confirm → readback → ack-write',
            'native_cleanup_validation':'offline_lifecycle_and_live_readonly_inspection_passed_actual_delete_pending',
            'native_selection_entry':'invoice_selection: HTTP pool and audited case manifest GUID set; no financial write',
            'native_create_entry':'invoice/create-draft → exact complete used_invoices readback → bind-serial; resume-draft reads original marker only',
            'native_create_limits':'mac_live_vat_and_source_bound_manual_receipts_created_with_independent_complete_ownership_readback; medical_procurement_preapproval_not_in_scope',
            'unknown_write':'recover_original_marker_then_independent_readback_no_replay',
            'native_travel_entry':'invoice_edit/run; inspect; original marker recovery; single existing journey, source-backed fields, exact airport catalog; actual save unaccepted',
            'native_medical_entry':'invoice_edit/medical-run; inspect; recover original marker',
            'native_medical_limits':'explicit_confirmed_self_pay_only; full_original_payment_fields_protected; actual_save_unaccepted',
            'native_amount_entry':'expense_amount inspect → open → inspect → ACK staged → save → inspect → ACK saved; recover original marker only',
            'native_amount_limits':'daily 20-digit complete per-group reduction; original-source SHA; native init/persistence refresh; W276 requires explicit full approved-funding leaf allocation and independent tree readback; eligibility-bound splits use correction',
            'native_split_entry':'correction inspect/apply/verify/recover; native_keys and source-backed two-funding special travel policy only',
            'native_returned_entry':'correction returned-prepare/open/confirm/recover; own rejected daily original, same serial and invoices, independent draft readback; no approval',
            'native_remuneration_entry':'access remuneration-inspect/lookup-person/add-person/create-claim/save/readback/recover; normal single-month student pay only; create requires bind-serial',
            'native_workflow_entry':'submission workflow-inspect/next/promise/department/workflow-readback/workflow-recover/applicant-prepare; exact promise hash and scoped authorization; no final applicant approval or SMS',
            'native_nonvat_entry':'invoice nonvat-inspect/save/readback/recover/select; original receipt SHA and native GUID, no invented VAT number',
            'native_allowance_entry':'ticketed domestic: native_bill_type=2 then domestic-allowance-inspect, domestic-travel-add-prepare/save/recover, domestic-allowance-edit-prepare/save/recover, domestic-allowance-finalize; standalone allowance-inspect/prepare/create/readback/recover requires explicit separate-claim source',
            'native_draft_cancellation':'draft-cancel-prepare/cancel/recover; explicit same-case serial/amount/source, own unsubmitted draft, complete invoice release readback; originals and history preserved',
            'native_supplement_validation':'mac_live_manual_receipts, counts_mode_allowance, same_claim_domestic_40_vat_plus_9_people_18_source_days_and_duplicate_draft_cancellation_independently_read_back; Windows live writes unverified',
            'remaining_write_adapters':'blocked_pending_native_adapter','ui_fallback':False}


def check_dispatch(request):
    operation = request['operation']
    p = request['parameters']
    action = p.get('action') or p.get('mode') or p.get('stage')
    execute = request['execute']
    if operation == 'access' and action is None:
        # canonical_job_request removes the default action for idempotency.
        action = 'ensure' if execute else 'check'
    blocked = False
    replacement = 'guide(topic="business")'
    if operation in {'stage', 'invoice', 'correction', 'submission', 'invoice_selection',
                     'invoice_retry', 'invoice_edit', 'expense_amount'} and execute:
        from finance_native_stage import STAGES
        blocked = not (operation == 'stage' and p.get('stage') in STAGES or operation=='invoice' and action in {'upload','upload-resume','upload-release','upload-recognize','upload-recognize-resume','upload-recover-recognition','create-draft','resume-draft','nonvat-save','nonvat-select','allowance-create','draft-cancel','domestic-travel-add-save','domestic-allowance-edit-save'} or operation=='submission'and action in {'options','next','promise','department','applicant-agree','applicant-refresh','applicant-documents'})
    if operation=='invoice_selection' and execute:
        from finance_native_selection import native_plan_request
        blocked=not native_plan_request(request)
    if operation=='invoice_edit'and execute and action in {'medical-run','run'}:
        blocked=False # 正式命令层仍核登记的医疗schema、逐票原件SHA与本人自费确认。
    if operation=='expense_amount'and execute:
        from finance_native_amount import native_plan_request
        blocked=not native_plan_request(request)
    if operation=='correction'and execute and action=='apply'and isinstance(p.get('plan'),dict)and p['plan'].get('native_keys'):
        blocked=False # 正式owner仍逐原件SHA、身份、专项资格、团队/个人上限和完整原生两项经费键核对。
    if operation=='correction'and execute and action in {'returned-open','returned-confirm'}:
        blocked=False # 正式owner绑定原单纠正授权、拒绝状态、完整原票及独立同号草稿回读。
    if operation=='invoice_retry' and execute:
        from finance_native_retry import native_plan_request
        blocked=not native_plan_request(request)
    elif operation == 'read_system' and p.get('mode') == 'open-draft':
        blocked = True
        replacement = 'business(action="bill-read", domain=已适配业务域)'
    elif operation == 'pdf_export' and execute and p.get('export_plan', {}).get('view_kind') not in {'formal_print_api','details'}:
        blocked = True
        replacement = 'print_batch正式HTTP打印或pdf_export view_kind=details明细HTTP预览'
    elif operation == 'invoice_verification' and action in {'view', 'query-used', 'refresh'}:
        blocked = True
        replacement = 'business(action="invoices-list")或business(action="bills-list")；其他范围待适配'
    elif operation=='invoice_verification' and action=='full-grid':
        import re
        blocked=p.get('grid')not in {'53','113','101'} or not isinstance(p.get('expected_userid'),str) or re.fullmatch(r'[A-Za-z0-9]{2,40}',p['expected_userid'])is None
        replacement='固定HTTP完整53/113/101查询，须明确当前账号'
    elif operation == 'access' and execute and action not in {
        'ensure', 'new-interface', 'wake-interface', 'check-page', 'restore-context', 'verify-reference', 'inspect', 'tabs', 'applicant-verification-open',
        'remuneration-add-person','remuneration-create-claim','remuneration-save'
    }:
        blocked = True
    if operation=='access' and execute and action=='wake-interface':
        from finance_backend import mac_native_enabled
        blocked=not mac_native_enabled()
        replacement='Mac Native Messaging原文档恢复；其他平台未接入，不退回旧界面唤醒'
    if blocked:
        raise ValueError('native_http_adapter_required: ' + operation + '/' + str(action) +
                         '仍依赖旧界面适配，已在派发前停止；使用' + replacement + '，不回退模拟点击')
