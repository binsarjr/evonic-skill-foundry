import json
import os
import shutil
import sys
import tempfile
import types
import unittest

from _package import load


materializer = load("materializer")


class FakeDB:
    def __init__(self):
        self.assignments = {"agent": []}

    def get_agents(self):
        return [{"id": "agent"}]

    def get_agent(self, agent_id):
        return {"id": agent_id} if agent_id == "agent" else None

    def get_agent_skills(self, agent_id):
        return list(self.assignments.get(agent_id, []))

    def set_agent_skills(self, agent_id, skills):
        self.assignments[agent_id] = list(skills)


class FakeSkillsManager:
    def __init__(self, root):
        self.root = root
        self.enabled = {}

    def install_skill_from_dir(self, source, force=False):
        with open(os.path.join(source, "skill.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        destination = os.path.join(self.root, manifest["id"])
        if os.path.exists(destination):
            if not force:
                return {"error": "already installed"}
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
        return manifest

    def get_skill(self, skill_id):
        path = os.path.join(self.root, skill_id)
        manifest_path = os.path.join(path, "skill.json")
        if not os.path.isfile(manifest_path):
            return None
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        manifest.update({"_dir": path, "enabled": self.enabled.get(skill_id, False)})
        return manifest

    def list_skills(self):
        return [self.get_skill(name) for name in sorted(os.listdir(self.root))]

    def set_skill_enabled(self, skill_id, enabled):
        if not self.get_skill(skill_id):
            return {"error": "missing"}
        self.enabled[skill_id] = enabled
        return self.get_skill(skill_id)

    def uninstall_skill(self, skill_id):
        shutil.rmtree(os.path.join(self.root, skill_id))
        return {"success": True}


class MaterializerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.manager = FakeSkillsManager(self.directory.name)
        self.db = FakeDB()
        backend = sys.modules.setdefault("backend", types.ModuleType("backend"))
        skills_module = types.ModuleType("backend.skills_manager")
        skills_module.SKILLS_DIR = self.directory.name
        skills_module.skills_manager = self.manager
        backend.skills_manager = skills_module
        sys.modules["backend.skills_manager"] = skills_module
        models = sys.modules.setdefault("models", types.ModuleType("models"))
        db_module = types.ModuleType("models.db")
        db_module.db = self.db
        models.db = db_module
        sys.modules["models.db"] = db_module

    def tearDown(self):
        self.directory.cleanup()

    @staticmethod
    def candidate(action="create", candidate_id="c1"):
        return {
            "id": candidate_id,
            "action": action,
            "slug": "demo",
            "title": "Demo",
            "description": "Reusable demo procedure",
            "brief": "Use when demonstrating a workflow",
            "system_md": "# Demo\n\nFirst version.",
            "agent_id": "agent",
            "session_id": "session",
            "skill_id": "generated-demo" if action == "update" else None,
        }

    def test_create_then_update_preserves_id_and_assignment(self):
        skill_id = materializer.materialize(self.candidate())
        self.assertEqual(skill_id, "generated-demo")
        self.assertEqual(self.manager.get_skill(skill_id)["version"], "0.1.0")
        materializer.enable(skill_id)
        materializer.assign(skill_id, "agent")

        update = self.candidate("update", "c2")
        update["system_md"] = "# Demo\n\nSecond version."
        self.assertEqual(materializer.materialize(update), skill_id)
        self.assertEqual(self.manager.get_skill(skill_id)["version"], "0.1.1")
        self.assertEqual(self.db.get_agent_skills("agent"), [skill_id])
        with open(
            os.path.join(self.directory.name, skill_id, "SYSTEM.md"), encoding="utf-8"
        ) as handle:
            self.assertIn("Second version", handle.read())

        update["agent_id"] = "other"
        with self.assertRaisesRegex(ValueError, "not owned"):
            materializer.materialize(update)

        source = os.path.join(self.directory.name, skill_id)
        suffixed_id = f"{skill_id}-v2"
        destination = os.path.join(self.directory.name, suffixed_id)
        os.rename(source, destination)
        manifest_path = os.path.join(destination, "skill.json")
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        manifest["id"] = suffixed_id
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        self.assertEqual(
            materializer.find_owned_by_slug("demo", "agent")["id"], suffixed_id
        )


if __name__ == "__main__":
    unittest.main()
