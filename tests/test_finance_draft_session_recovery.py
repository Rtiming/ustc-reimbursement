import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from finance_draft_session_recovery import FIELDS, digest, verify
from select_case_invoices import select


class SessionRecoveryTests(unittest.TestCase):
    def fixture(self, root):
        def save(name, value):
            p = root/name; p.write_text(json.dumps(value)); return p
        original = root/'synthetic.pdf'; original.write_bytes(b'synthetic invoice')
        number = '12345678901234567890'
        row = {'t.tk_guid':'G1','t.tk_code_abst':'普/全电票/'+number,'t.tk_type':'VAT',
               't.tk_total_amt':'12.00','t.serial_no':'','t.tk_mod_flag':'NORMAL'}
        def observed(func, rows, minute, tab='2'):
            return {'phase':'script_completed','tab_id':tab,'executed_at_utc':f'2026-01-02T00:{minute:02}:00+00:00',
                    'result':{'ok':True,'projid':'WF_YBX','serial':'','visibleDialogs':[], 'scopeComplete':True,
                              'identity':{'userid':'TEST01'},'grids':[{'funcno':func,'records':len(rows),'rows':rows}]}}
        pool = observed('53',[row],5)
        m = {'case_id':'test_case','serial':None,'expected_count':1,'expected_amount':'12.00',
             'original_files':[{'path':str(original),'sha256':digest(original),'invoice_number_candidates':[number]}]}
        m = select(m,pool,'12.00')
        template = (Path(__file__).resolve().parents[1]/'tools/create_daily_draft.js').read_text()
        script=root/'create.js'; script.write_text('(()=>{if(location.origin!=="https://cwzh.ustc.edu.cn"||$.getProjID()!=="WF_YBX")throw Error("财务系统不符");return (('+template+')('+json.dumps(m)+'));})()')
        sources = {
            'creation': {'phase':'script_completed','tab_id':'1','script':str(script),'source_sha256':digest(script),
                         'executed_at_utc':'2026-01-02T00:00:00+00:00','result':{'ok':True,'action':'create_daily_draft_requested','case_id':'test_case','count':1,'total':'12.00'}},
            'surface': {'phase':'script_completed','tab_id':'1','executed_at_utc':'2026-01-02T00:01:00+00:00',
                        'result':{'ok':True,'identity':{'userid':'TEST01'},'previewDiagnostics':{'resources':[{'path':'/YBX/commonUpdate_responseButtonEvent.action','responseStatus':404}]}}},
            'opening': {'case_id':'test_case','old_tab_id':'1','page_opened':True,'new_tab_id':'3','started_at_utc':'2026-01-02T00:02:00+00:00',
                        'after':{'tabs':[{'tab_id':'3','path':'https://cwzh.ustc.edu.cn/YBX/login.html'}]}},
            'access': {'tab_id':'3','finance_tab_id':'2','observed_at_utc':'2026-01-02T00:03:00+00:00','result':{'state':'ready','login_verified':True}},
            'drafts': observed('101',[{'t.req_date':'2026-01-01'}],4), 'pool':pool}
        refs = {k:{'path':str(save(k+'.json',v))} for k,v in sources.items()}
        for ref in refs.values(): ref['sha256']=digest(ref['path'])
        m.update(creation_receipt=refs['creation']['path'],draft_creation_requested=True)
        return m, {'manifest':{k:copy.deepcopy(m.get(k)) for k in FIELDS},'expected_userid':'TEST01','sources':refs}, sources

    def test_full_evidence_and_fail_closed_conditions(self):
        with tempfile.TemporaryDirectory() as temp:
            m, proof, sources = self.fixture(Path(temp))
            self.assertEqual(verify(m,proof,sources['pool'])['state'],'expired_session_not_created')
            mutations = [
                ('drafts',lambda d:d['result']['grids'][0]['rows'][0].update({'t.req_date':'2026-01-02'})),
                ('pool',lambda d:d['result']['grids'][0]['rows'][0].update({'t.serial_no':'123'})),
                ('pool',lambda d:d['result']['grids'][0].update(records=2)),
                ('pool',lambda d:d['result']['identity'].update(userid='OTHER')),
                ('surface',lambda d:d['result']['previewDiagnostics']['resources'][0].update(responseStatus=200)),
                ('access',lambda d:d.update(finance_tab_id='1'))]
            for kind, change in mutations:
                d=copy.deepcopy(sources[kind]);change(d);p=Path(proof['sources'][kind]['path']);p.write_text(json.dumps(d))
                proof['sources'][kind]['sha256']=digest(p)
                with self.assertRaises(ValueError):verify(m,proof)
                p.write_text(json.dumps(sources[kind]));proof['sources'][kind]['sha256']=digest(p)
            changed=copy.deepcopy(m);changed['expected_amount']='13.00'
            with self.assertRaises(ValueError):verify(changed,proof)
            with self.assertRaises(ValueError):verify(m,proof,{**sources['pool'],'tab_id':'9'})


if __name__ == '__main__': unittest.main()
