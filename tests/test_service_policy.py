import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import evonic_skill_foundry.service as service

class PolicyTests(unittest.TestCase):
    def test_auto_assign_requires_enable(self):
        self.assertTrue(service.config_errors({'AUTO_ASSIGN_GENERATED_SKILLS':True}))
        self.assertFalse(service.config_errors({'AUTO_ASSIGN_GENERATED_SKILLS':True,'AUTO_ENABLE_GENERATED_SKILLS':True}))
if __name__=='__main__': unittest.main()
