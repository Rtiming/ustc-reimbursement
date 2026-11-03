"""分类初始化已退出的原单回读证明，使用合成材料。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from finance_native_categories import verify_categories_ack
from finance_native_stage import digest

class CategoriesRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.c={k:[] for k in ['funding','funding_policy','categories_contract','amount_contract','submission_contract','basic_contracts','used_contracts']}
        self.c.update(stage='categories',case_id='fixture',serial='123',expected_userid='TEST001',expected_amount='91.00',tab_id='11',
                      expense_categories=[{'group':'纸制品','subject':'3029901','subject_name':'学生活动费'}])
        self.plan={'original':{'bill':{'serial':'123'},'basic':[],'invoices':[{'guid':'A'}],
                   'categories':[{'group':'纸制品','group_key':'纸制品!P!1','subject':'30201','subject_name':'办公费','project':'F1','budget':'B1','parameters':'纸制品!P!1&30201&F1&B1'}]},
                   'changes':{'纸制品!P!1':{'subject':'3029901','subject_name':'学生活动费'}},'definition_sha256':'synthetic'}
        self.source="ReimbursementNativeProtocol.response(raw,[]).accepted\nawait execute({check_func:'',procs:win.beforeLoadProcs},context);"
        (self.root/'native-stage-adapter.js').write_text(self.source)
        script=self.root/'save.js';script.write_text('(async()=>{\n'+self.source+'\nreturn await reimbursementCategoriesSave('+json.dumps({**self.c,'expected_plan':self.plan},ensure_ascii=False)+');\n})()')
        self.saved={'script':str(script),'source_sha256':digest(script),'result':{'ok':False,'save_dispatched':True,'system_write':True,'state':'unknown_save_readback_required','error':'NATIVE_PROTOCOL_LOCAL_KEY','completed':[],
                    'requests':[{'endpoint':'loadDefinition.action','status':'received'},{'endpoint':'commonUpdate_responseButtonEvent.action','status':'received'}]}}
        read=self.root/'read.js';read.write_text('(async()=>{\n'+self.source+'\nreturn await reimbursementCategoriesPrepare('+json.dumps(self.c,ensure_ascii=False)+');\n})()')
        self.read={'tab_id':'11','script':str(read),'source_sha256':digest(read),'result':copy.deepcopy(self.plan)}
        # 通用plan身份/时序由stage verifier另测；这里专测分类原文与证据约束。
        p=patch('finance_native_categories.validate_plan');p.start();self.addCleanup(p.stop)

    def test_completed_initialization_and_exact_original_allow_not_applied(self):
        self.assertTrue(verify_categories_ack(self.c,self.plan,self.saved,self.read,'not_applied')['original_unchanged'])

    def test_selected_response_can_be_acknowledged_only_with_all_persistent_fields(self):
        source=self.source+"\nphase='select';await execute("
        (self.root/'native-stage-adapter.js').write_text(source)
        for entry in [self.saved,self.read]:
            p=Path(entry['script']);p.write_text(p.read_text().replace(self.source,source));entry['source_sha256']=digest(p)
        self.saved['result'].update(failed_phase='select',error='CATEGORIES_API_LOCAL_SCOPE',native_response='passlocal(61-xtype:纸制品!P!1&3029901 &F1&)',
            native_responses=[{'phase':'initialize','status':'pass'}],requests=[{'endpoint':e,'status':'received'}for e in ['loadDefinition.action','commonUpdate_responseButtonEvent.action','commonQuery_doQuery.action','commonUpdate_responseButtonEvent.action']])
        row=self.read['result']['original']['categories'][0];row.update(subject='3029901',subject_name='学生活动费',parameters='纸制品!P!1&3029901&F1&B1')
        self.assertTrue(verify_categories_ack(self.c,self.plan,self.saved,self.read,'saved')['completed_native_selection_readback'])
        for field,value in [('project','OTHER'),('budget','OTHER'),('subject','30201')]:
            bad=copy.deepcopy(self.read);bad['result']['original']['categories'][0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):verify_categories_ack(self.c,self.plan,self.saved,bad,'saved')
        self.saved['result']['native_response']='judge:confirm'
        with self.assertRaises(ValueError):verify_categories_ack(self.c,self.plan,self.saved,self.read,'saved')

    def test_any_other_original_change_blocks(self):
        for key in self.plan['original']:
            value=copy.deepcopy(self.read);value['result']['original'][key]=[]
            if value['result']['original'][key]==self.plan['original'][key]:value['result']['original'][key]=['changed']
            with self.subTest(key=key),self.assertRaises(ValueError):verify_categories_ack(self.c,self.plan,self.saved,value,'not_applied')

    def test_inflight_partial_save_unknown_reply_and_wrong_tab_block(self):
        for change in [{'error':'timeout'},{'completed':[{'group_key':'纸制品!P!1','subject':'3029901'}]},
                       {'requests':self.saved['result']['requests']+[{'endpoint':'save','status':'received'}]}]:
            value={**self.saved,'result':{**self.saved['result'],**change}}
            with self.subTest(change=change),self.assertRaises(ValueError):verify_categories_ack(self.c,self.plan,value,self.read,'not_applied')
        with self.assertRaises(ValueError):verify_categories_ack(self.c,self.plan,self.saved,{**self.read,'tab_id':'12'},'not_applied')
        with self.assertRaises(ValueError):verify_categories_ack(self.c,self.plan,self.saved,self.read,'saved')

if __name__=='__main__':unittest.main()
