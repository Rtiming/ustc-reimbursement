"""Synthetic offline fixtures. Files are created only inside a test-owned directory."""
import hashlib
from pathlib import Path


def correction_plan(directory):
    people = []
    for index in range(1, 3):
        source = Path(directory) / f'synthetic-invoice-{index}.txt'
        source.write_text(f'synthetic invoice {index}; no financial validity\n')
        people.append({
            'name': f'测试人员{index}', 'person_id': f'TEST{index:03}',
            'source_file': str(source), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'invoice_guid': f'synthetic-guid-{index}', 'invoice_number': f'{99000000000000000000 + index}',
            'invoice_amount': '500.00', 'wc_amount': '450.00',
            'group': '交通运输服务' if index == 1 else '飞机行程单',
            'source_kind': '去程机票', 'route': '测试出发地—测试到达地',
        })
    return {
        'case_id': 'synthetic_correction', 'serial': '123456789', 'amount': '1000.00',
        'funding': [{'code': 'WC0000000001', 'amount': '900.00'}, {'code': 'WW0000000002', 'amount': '100.00'}],
        'people': people,
        'policy': {'project': 'WC0000000001', 'subject': '30211', 'budget': 'A00001',
                   'team_cap': '900.00', 'person_cap': '800.00', 'target': '900.00',
                   'allowed_kinds': ['去程机票', '返程机票', '去程火车票', '返程火车票']},
        'expected_groups': ['交通运输服务', '飞机行程单'],
        'remainder_subjects': {'交通运输服务': '30211', '飞机行程单': '30211'},
        'native_groups': {},
    }
