import os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from repository import Repository
from validator import validate_candidate

class Tests(unittest.TestCase):
    def test_idempotent_job(self):
        with tempfile.TemporaryDirectory() as d:
            r=Repository(os.path.join(d,'db.sqlite'))
            self.assertTrue(r.create_job('k','a','s'))
            self.assertIsNone(r.create_job('k','a','s'))
    def test_validator_blocks_secret(self):
        errors,risks=validate_candidate({'slug':'safe','title':'Safe','description':'A sufficiently descriptive generated procedure','brief':'Use when a repeatable procedure applies','system_md':'Use token ghp_abcdefghijklmnopqrstuvwxyz1234567890'})
        self.assertTrue(errors)
        self.assertTrue(any(x['severity']=='critical' for x in risks))
if __name__=='__main__': unittest.main()
