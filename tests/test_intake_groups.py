from pathlib import Path
import io
import sys
import tempfile
import time
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from assistant_api import AssistantService,read_json,atomic_json
from material_intake_service import configure_bundled_archive

class IntakeGroupsTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.service=AssistantService(self.root,[self.root]);self.case='incoming';self.service.new_case(self.case)
        archive=self.root/'two activities.zip'
        import fitz
        originals={}
        for name,number in [('A','9'*20),('B','8'*20)]:
            with fitz.open()as pdf:
                pdf.new_page().insert_text((40,50),'SYNTHETIC '+name+' '+number);originals[name]=pdf.tobytes()
        self.originals=originals
        with zipfile.ZipFile(archive,'w')as z:
            z.writestr('Activity A/invoice.pdf',originals['A']);z.writestr('Activity B/invoice.pdf',originals['B']);z.writestr('unassigned.txt','not classified')
        configure_bundled_archive(self.root)
        request={'schema':'reimbursement.material-intake.v1','case_id':self.case,'sources':[{'id':'received','path':str(archive),'group_strip_dirs':0}],'output_dir':str(self.root/'archived')}
        job=self.service.material_intake(self.case,request,'synthetic-groups',True);self.job=job['job_id']
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            result=self.service.result(self.case,self.job)
            if result['status']not in {'queued','running'}:break
            time.sleep(.1)
        self.assertEqual(result['status'],'completed',result)
        self.view=self.service.intake_groups(self.case,self.job)
        self.groups=[{'title':r['original_summary'],'material_ids':r['material_ids']}for r in self.view['directory_candidates']]

    def test_explicit_split_preserves_source_scope_and_reconnect(self):
        preview=self.service.intake_groups(self.case,self.job,self.groups)
        self.assertEqual(preview['unassigned_count'],1);self.assertEqual(len(self.service.cases()['cases']),1)
        result=self.service.intake_groups(self.case,self.job,self.groups,self.view['revision'],True)
        self.assertEqual(len(result['created_cases']),2)
        for child in result['created_cases']:
            case=child['case_id'];batches=self.service.intake_review(case,action='batches')
            self.assertEqual(len(batches['batches']),1)
            files=self.service.intake_review(case,self.job);self.assertEqual(files['total_materials'],1)
            self.assertFalse(files['can_split_activities'])
            row=files['rows'][0]
            letter=child['title'][-1];number=('9'if letter=='A'else'8')*20
            self.assertEqual(row['invoice_candidates'],[{'number':number,'page':1}])
            picture=self.service.intake_review(case,self.job,action='preview',material_id=row['material_id'])
            self.assertTrue(Path(picture['preview_path']).is_file())
            self.assertEqual(Path(picture['source_path']).read_bytes(),self.originals[letter])
            self.service.intake_review(case,self.job,action='register',records=[{'material_id':row['material_id'],'role':'invoice','invoice_records':[{'number':number,'page':1}]}],expected_revision=self.service.revision(case),confirmation_note='synthetic source only',execute=True)
            self.assertIsNone(read_json(self.service.resolve(case)[1]['form'])['serial'])
        again=self.service.intake_groups(self.case,self.job,self.groups,self.view['revision'],True)
        self.assertTrue(again['saved']);self.assertEqual(len(self.service.cases()['cases']),3)

    def test_duplicate_scope_and_different_second_plan_rejected(self):
        duplicate=[self.groups[0],dict(self.groups[0],title='duplicate')]
        with self.assertRaises(ValueError):self.service.intake_groups(self.case,self.job,duplicate)
        result=self.service.intake_groups(self.case,self.job,self.groups,self.view['revision'],True)
        changed=[dict(self.groups[0],title='another title'),self.groups[1]]
        with self.assertRaises(ValueError):self.service.intake_groups(self.case,self.job,changed,self.view['revision'],True)
        child=result['created_cases'][0]['case_id'];_,paths=self.service.resolve(child);case=read_json(paths['case'])
        case['material_context']['intake_group']['material_ids']=self.groups[1]['material_ids'];atomic_json(paths['case'],case)
        with self.assertRaises(ValueError):self.service.intake_review(child,self.job)

    def test_group_can_accept_own_supplements_without_reading_sibling_scope(self):
        result=self.service.intake_groups(self.case,self.job,self.groups,self.view["revision"],True)
        child,sibling=[row["case_id"] for row in result["created_cases"]]
        payment=self.root/"payment.txt";payment.write_text("Synthetic paid 10.00",encoding="utf-8")
        job=self.service.material_intake(child,{"schema":"reimbursement.material-intake.v1","case_id":child,
            "sources":[{"id":"payment","path":str(payment),"group_strip_dirs":None}],
            "output_dir":str(self.root/"supplements")},"group-supplement",True)
        deadline=time.monotonic()+30
        while job["status"] in {"queued","running"} and time.monotonic()<deadline:
            time.sleep(.1);job=self.service.result(child,job["job_id"])
        self.assertEqual(job["status"],"completed",job)
        batches=self.service.intake_review(child,action="batches")
        self.assertEqual(len(batches["batches"]),2)
        self.assertEqual(self.service.intake_review(child,self.job)["total_materials"],1)
        self.assertEqual(self.service.intake_review(child,job["job_id"])["total_materials"],1)
        combined=self.service.case_materials(child)
        self.assertEqual(combined["total_materials"],2)
        with self.assertRaises(ValueError):self.service.intake_review(sibling,job["job_id"])

if __name__=='__main__':unittest.main()
