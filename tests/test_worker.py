import importlib.util
import tempfile
import pathlib
import unittest
from unittest.mock import patch

script=pathlib.Path(__file__).parents[1]/'github'/'worker'/'worker.py'
spec=importlib.util.spec_from_file_location('re_worker',script)
worker=importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

class Tests(unittest.TestCase):
    def test_patch_requires_exact_match(self):
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/'input.smali';p.write_text('.class test\n.method hi\n  nop\n.end method\n')
            with patch.object(worker,'OUTPUT',pathlib.Path(d)):
                res=worker.exact_patch({'patch_spec':{'path':'','find':'  nop','replace':'  return-void'}},p,pathlib.Path(d))
                self.assertTrue(res['patch_applied'])
                self.assertIn('return-void',p.read_text())
    def test_patch_refuses_ambiguous(self):
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/'input.smali';p.write_text('nop\nnop\n')
            with self.assertRaisesRegex(ValueError,'found 2'):
                worker.exact_patch({'patch_spec':{'find':'nop','replace':'return-void'}},p,pathlib.Path(d))
    def test_valid_smali_inventory(self):
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/'input.smali';p.write_text('.class public LTest;\n.method test()V\n.end method\n')
            a=worker.analyze(p,pathlib.Path(d))
            self.assertEqual(a['method_count'],1)

if __name__=='__main__':unittest.main()
