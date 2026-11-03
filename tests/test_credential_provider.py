"""独立密码库协议：固定消费者、隔离配置和明确失败，不访问真实密码库。"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from credential_provider import configuration,invocation


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.conf={'username':'TEST001','credential_reference':'owner.sso','credential_backend':'rtime-credentials','secret_values_in_project':False}

    def test_separate_operator_vault_does_not_change_parent_environment(self):
        tool=self.root/'provider.py';tool.write_text('')
        vault=self.root/'separate.json';vault.write_text('{}');vault.chmod(0o600)
        conf={**self.conf,'credential_cli':str(tool),'credential_config':str(vault)}
        path=self.root/'access.json';path.write_text(json.dumps(conf))
        parsed,digest=configuration(path)
        with patch.dict(os.environ,{'RTIME_CREDENTIAL_CONFIG':'original','FINANCE_CAS_PASSWORD':'synthetic-parent'}):
            command,env=invocation(parsed)
            self.assertEqual(env['RTIME_CREDENTIAL_CONFIG'],str(vault));self.assertNotIn('FINANCE_CAS_PASSWORD',env)
            self.assertEqual(os.environ['RTIME_CREDENTIAL_CONFIG'],'original')
        self.assertEqual(command,[sys.executable,str(tool),'use']);self.assertEqual(len(digest),64)
        vault.write_text('{"changed":true}');vault.chmod(0o600)
        self.assertNotEqual(configuration(path)[1],digest)

    def test_fixed_external_json_provider_and_no_secret_configuration(self):
        conf={**self.conf,'credential_backend':'json-command','credential_command':[sys.executable,str(self.root/'provider.py')]}
        path=self.root/'access.json';path.write_text(json.dumps(conf));parsed,_=configuration(path)
        self.assertEqual(invocation(parsed)[0],conf['credential_command']+['use'])
        for field,value in [('password','secret'),('credential_command',['relative-tool']),('credential_backend','guess-any-vault')]:
            bad={**conf,field:value};path.write_text(json.dumps(bad))
            with self.subTest(field=field),self.assertRaises(ValueError):configuration(path)

    @unittest.skipIf(os.name=='nt','Windows ACL由部署工具维护')
    def test_nonprivate_or_missing_provider_config_is_rejected(self):
        tool=self.root/'provider.py';tool.write_text('');vault=self.root/'vault.json';vault.write_text('{}');vault.chmod(0o644)
        conf={**self.conf,'credential_cli':str(tool),'credential_config':str(vault)}
        with self.assertRaisesRegex(ValueError,'not_private'):invocation(conf)
        vault.unlink()
        with self.assertRaisesRegex(ValueError,'unavailable'):invocation(conf)
