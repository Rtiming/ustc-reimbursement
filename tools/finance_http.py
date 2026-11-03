"""Compose only registered school adapters with their shared HTTP runtime."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ADAPTERS = {'amount':['finance_business_api.js','finance_native_protocol.js','finance_native_form.js','finance_submission_api.js','finance_amount_api.js'], 'retry': ['finance_business_api.js', 'finance_native_protocol.js', 'finance_native_form.js', 'finance_retry_api.js'], 'business': ['finance_preview_api.js', 'finance_business_api.js'], 'print': ['finance_print_api.js'],
            'travel':['finance_business_api.js','finance_native_protocol.js','finance_native_form.js','finance_travel_api.js'],
            'medical':['finance_business_api.js','finance_native_protocol.js','finance_native_form.js','finance_medical_api.js'],
            'submission': ['finance_business_api.js','finance_native_protocol.js','finance_native_form.js','finance_submission_api.js'],
            'create': ['finance_business_api.js', 'finance_native_protocol.js', 'finance_native_form.js', 'finance_create_api.js'],
            'upload': ['finance_business_api.js', 'finance_native_protocol.js', 'finance_native_form.js', 'finance_upload_api.js'],
            'details': ['finance_business_api.js', 'finance_native_protocol.js', 'finance_native_form.js', 'finance_details_api.js'],
            'health': ['finance_backend_health.js'],
            'grid-export': ['finance_preview_api.js', 'finance_business_api.js', 'finance_grid_export_api.js'],
            'notes': ['finance_business_api.js', 'finance_native_protocol.js', 'finance_native_form.js', 'finance_notes_api.js'],
            'pre-review': ['finance_preview_api.js', 'finance_business_api.js', 'finance_native_protocol.js', 'finance_native_form.js', 'finance_pre_review_api.js'],
            'funding': ['finance_business_api.js', 'finance_native_protocol.js', 'finance_funding_api.js'],
            'payments': ['finance_business_api.js', 'finance_native_protocol.js', 'finance_corporate_api.js', 'finance_payments_api.js'],
            'expenses': ['finance_business_api.js', 'finance_native_protocol.js', 'finance_expenses_api.js'],
            'basic': ['finance_business_api.js', 'finance_native_protocol.js', 'finance_basic_api.js'],
            'staff': ['finance_native_protocol.js', 'finance_native_form.js', 'finance_staff_api.js']}


def source(adapter):
    if adapter=='approver':
        return '\n'.join((ROOT/name).read_text(encoding='utf-8')for name in ['finance_http.js','finance_business_api.js','finance_native_protocol.js','finance_native_form.js','finance_approver_api.js'])
    if adapter=='applicant':
        return '\n'.join((ROOT/name).read_text(encoding='utf-8')for name in ['finance_http.js','finance_business_api.js','finance_native_protocol.js','finance_native_form.js','finance_applicant_api.js'])
    if adapter=='submission':
        return '\n'.join((ROOT/name).read_text(encoding='utf-8')for name in ['finance_http.js',*ADAPTERS['amount'],'finance_remuneration_api.js'])
    if adapter in {'funding','expenses'}:
        return '\n'.join((ROOT/name).read_text(encoding='utf-8')for name in ['finance_http.js',*ADAPTERS['amount'],'finance_categories_api.js','finance_remuneration_api.js','finance_default_funding_api.js','finance_allowance_api.js','finance_domestic_allowance_api.js','finance_'+adapter+'_api.js'])
    if adapter in {'categories','split','remuneration','workflow'}:
        return '\n'.join((ROOT/name).read_text(encoding='utf-8')for name in ['finance_http.js',*ADAPTERS['amount'],'finance_categories_api.js','finance_categories_candidates.js',*(['finance_split_api.js']if adapter=='split'else['finance_preview_api.js','finance_corporate_api.js','finance_payments_api.js','finance_default_funding_api.js','finance_allowance_api.js','finance_domestic_allowance_api.js','finance_expenses_api.js','finance_pre_review_api.js','finance_remuneration_api.js','finance_workflow_api.js']if adapter=='workflow'else['finance_remuneration_api.js']if adapter=='remuneration'else[])])
    if adapter not in ADAPTERS:
        raise ValueError('未知固定HTTP适配器')
    return '\n'.join((ROOT / name).read_text(encoding='utf-8')
                     for name in ['finance_http.js', *ADAPTERS[adapter]])
