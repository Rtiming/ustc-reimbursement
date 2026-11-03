"""持久回读使用Python独立重算，覆盖经费叶计划和保护字段。"""
import copy,json,subprocess,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from finance_native_amount import allocation_target

class AllocationReadbackTests(unittest.TestCase):
    def fixture(self):
        guid='A'*32
        def row(key,parent,leaf,project,amount):
            return {'t.ord':key,'t.tparent':parent,'t.tleaf':str(leaf).lower(),'t.tk_guid':guid,'t.uni_prj_code':project,'t.bu_code':'B1'if project else'',
                    't.t_amt':amount,'t.invoice_amt':'12.00','t.amt_sum':'12.00','t.old_amt':'12.00','t.t_desc':'protected'}
        rows=[row('R','0',False,'','10.00'),row('A','R',True,'F1','7.00'),row('B','R',True,'F2','5.00')]
        c={'funding':[{'code':'F1','amount':'6.00'},{'code':'F2','amount':'4.00'}],'funding_policy':{},'reduction':{'rows':[{'invoice_guid':guid,'claim_amount':'10.00'}]},
           'budget_allocations':[{'invoice_guid':guid,'project':'F1','budget':'B1','amount':'6.00'},{'invoice_guid':guid,'project':'F2','budget':'B1','amount':'4.00'}]}
        return c,rows,[guid]
    def test_recompute_and_preserve_hidden_fields(self):
        c,rows,owned=self.fixture();before=copy.deepcopy(rows);target=allocation_target(c,rows,owned)
        self.assertEqual([r['t.t_amt']for r in target],['10.00','6.00','4.00']);self.assertEqual(rows,before)
        for old,new in zip(rows,target):self.assertEqual({k:v for k,v in old.items()if k!='t.t_amt'},{k:v for k,v in new.items()if k!='t.t_amt'})
    def test_reject_wrong_plan_owner_ancestry_total_budget_and_cap(self):
        for mode in ['missing','duplicate','foreign','orphan','cycle','total','budget','cap','policy']:
            c,r,o=self.fixture()
            if mode=='missing':c['budget_allocations'].pop()
            if mode=='duplicate':c['budget_allocations'][1]=c['budget_allocations'][0]
            if mode=='foreign':o=['B'*32]
            if mode=='orphan':r[1]['t.tparent']='absent'
            if mode=='cycle':r[0]['t.tparent']='A'
            if mode=='total':c['budget_allocations'][0]['amount']='5.00'
            if mode=='budget':r[1]['t.bu_code']='B2'
            if mode=='cap':c['budget_allocations'][0]['amount']='8.00';c['budget_allocations'][1]['amount']='2.00'
            if mode=='policy':c['funding_policy']['invoice_level_required']=True
            with self.subTest(mode=mode),self.assertRaises(ValueError):allocation_target(c,r,o)

if __name__=='__main__':unittest.main()
