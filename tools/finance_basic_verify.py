"""Pure verification of registered native basic fields; no UI or case mutation."""
from assistant_api import APIError


def verify_basic_fields(form, frame):
    """Match unique native basic roles and raw text; neither input is modified."""
    fields, text = form['basic']['fields'], frame['visibleText']
    raw = frame.get('raw_basic')
    if raw is not None:
        if (not isinstance(raw, dict) or raw.get('schema') != 'ustc-native-basic-text-v1'
                or not isinstance(raw.get('fields'), list) or any(not isinstance(row, dict) for row in raw['fields'])):
            raise APIError('batch_basic_readback', '原生摘要备注原始文本结构不完整')
        for label, key in [('摘要', 't-abst'), ('备注', 't-cl_abst')]:
            if [row.get('value') for row in raw['fields'] if row.get('label') == label] != [fields[key]]:
                raise APIError('batch_basic_readback', '原生摘要或备注原样文本不符或不唯一')
    elif ('摘要\t' + fields['t-abst'] + '\n') not in text or ('备注\t' + fields['t-cl_abst']) not in text:
        raise APIError('batch_basic_readback', '摘要或系统备注未持久保存')
    def native_value(label):
        values = [row[i + 1] for table in frame.get('tables', []) for row in table
                  for i, cell in enumerate(row[:-1]) if cell == label]
        if len(values) != 1:
            raise APIError('batch_basic_readback', '基本信息原生标签不唯一或缺失：' + label)
        return values[0]
    if native_value('验收人/证明人工号') != form['basic']['teacher_id'] + ' ' + form['basic']['teacher_name']:
        raise APIError('batch_basic_readback', '原生验收人/证明人工号及姓名与本案不符')
    expected_phones = [fields.get(key) for key in ['t-req_mobile', 't-req_tele'] if fields.get(key)]
    if native_value('联系电话').split() != expected_phones:
        raise APIError('batch_basic_readback', '手机或联系电话尚未按本案保存')
    changefee = {'N': '不包含', 'Y': '包含'}.get(fields.get('t-is_have_changefee'))
    return_point = {'退单地点': '东区财务处', '退单地点2': '西区财务处'}.get(fields.get('t-td_point'))
    if (not changefee or not return_point or native_value('是否包含项目负责人费用') != changefee
            or native_value('退单地点') != return_point):
        raise APIError('batch_basic_readback', '项目负责人费用或退单地点未保存为已核原生选项')
    return form
