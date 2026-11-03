import unittest
import tempfile
import os
from pathlib import Path
from unittest.mock import patch
from assistant_api import atomic_json, read_json

class AtomicJsonTests(unittest.TestCase):
    @unittest.skipUnless(os.name=="nt","Windows??????")
    def test_transient_replace_retries_only_the_publish_and_permanent_failure_keeps_old_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"state.json";atomic_json(path,{"value":"old"})
            real=os.replace
            error=PermissionError("synthetic sharing violation");error.winerror=32
            calls=[]
            def transient(source,target):
                calls.append((source,target))
                if len(calls)==1:raise error
                return real(source,target)
            with patch("assistant_api.os.replace",side_effect=transient),patch("assistant_api.time.sleep"):
                atomic_json(path,{"value":"new"})
            self.assertEqual(len(calls),2);self.assertEqual(read_json(path),{"value":"new"})
            with patch("assistant_api.os.replace",side_effect=error),patch("assistant_api.time.sleep"):
                with self.assertRaises(PermissionError):atomic_json(path,{"value":"never published"})
            self.assertEqual(read_json(path),{"value":"new"})
