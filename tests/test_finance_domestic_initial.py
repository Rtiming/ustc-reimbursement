import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import finance_domestic_initial as native

class DomesticInitialTests(unittest.TestCase):
    def test_unknown_create_only_recovers_original_accepted_domestic_serial(self):
        c={'bill_type':'2','case_id':'fixture','expected_userid':'TEST001','token':'original'}
        created={'create_dispatched':True,'case_id':'fixture','expected_userid':'TEST001','token':'original','marker':{'native_responses':[{'phase':'create_requested','raw':'pass:local(0-g0Serialno:123456;0-bx_type:2;0-PreviewFlag:Y)'}]}}
        self.assertEqual(native.serial_from_original(c,created),'123456')
        for mutate in [lambda b:b.update(token='new'),lambda b:b.update(create_dispatched=False),lambda b:b['marker']['native_responses'][0].update(raw='error:no'),lambda b:b['marker']['native_responses'][0].update(raw='pass:local(0-g0Serialno:123456;0-bx_type:1)'),lambda b:b['marker']['native_responses'].append(copy.deepcopy(b['marker']['native_responses'][0]))]:
            bad=copy.deepcopy(created);mutate(bad)
            with self.assertRaises(ValueError):native.serial_from_original(c,bad)
        with self.assertRaises(ValueError):native.serial_from_original({**c,'bill_type':'1'},created)

if __name__=='__main__':unittest.main()
