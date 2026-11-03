"""历史归档引用完整本人清单；不将历史单号登记到当前未建单案件。"""
from finance_business_api import reference_context, validate


def reference_export_case(service, request):
    p = request['parameters']
    plan = p.get('export_plan')
    if not isinstance(plan, dict) or plan.get('view_kind') not in {'formal_print_api', 'details'}:
        raise ValueError('参考单归档只接受原生正式打印或明细HTTP导出')
    params = {'action': 'reference-bill-read', 'domain': 'basic', 'tab_id': p.get('tab_id'),
              'expected_userid': p.get('expected_userid'), 'source_job_id': p.get('source_job_id'),
              'reference_serial': plan.get('serial')}
    validate(params)
    context = reference_context(service, {'case_id': request['case_id'], 'parameters': params})
    source = context['reference_bill']
    if (plan.get('case_id') != request['case_id'] or plan.get('summary') != source['summary']
            or plan.get('business_date') != source['business_date']
            or str(plan.get('sequence')) != str(source['sequence'])
            or str(plan.get('amount')) != context['expected_amount']):
        raise ValueError('参考单导出的日期、归档序号、摘要及金额须与完整清单逐项相符')
    return {'case_id': request['case_id'], 'serial': context['serial'],
            'expected_userid': p['expected_userid'], 'amount': context['expected_amount'],
            'basic': {'fields': {'t-abst': source['summary']}},
            'reference_only': True, 'reference_source': context['reference_source']}
