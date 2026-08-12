import sys
import types
import unittest
from unittest.mock import Mock, patch

from _package import load


service = load("service")
handler = load("handler")


class PolicyTests(unittest.TestCase):
    def test_reviewer_retries_unsupported_forced_tool_choice(self):
        client = Mock()
        client.chat_completion.side_effect = [
            {
                "success": False,
                "error_detail": "Thinking mode does not support this tool_choice",
            },
            {"success": True, "response": {"choices": []}},
        ]
        result = service._request_review(client, [], [], {})
        self.assertTrue(result["success"])
        self.assertEqual(
            client.chat_completion.call_args_list[0].kwargs["tool_choice"],
            "submit_skill_review",
        )
        self.assertNotIn("tool_choice", client.chat_completion.call_args_list[1].kwargs)

    def test_agent_setting_defaults_enabled_without_plugin_manager(self):
        fake_db = Mock()
        fake_db.get_setting.side_effect = [None, "0"]
        models = types.ModuleType("models")
        db_module = types.ModuleType("models.db")
        db_module.db = fake_db
        models.db = db_module
        with patch.dict(sys.modules, {"models": models, "models.db": db_module}):
            self.assertTrue(handler._agent_enabled("new-agent"))
            self.assertFalse(handler._agent_enabled("opted-out-agent"))

    def test_auto_assign_requires_enable(self):
        self.assertTrue(service.config_errors({"AUTO_ASSIGN_GENERATED_SKILLS": True}))
        self.assertFalse(
            service.config_errors(
                {
                    "AUTO_ASSIGN_GENERATED_SKILLS": True,
                    "AUTO_ENABLE_GENERATED_SKILLS": True,
                }
            )
        )

    @patch.object(service, "find_owned_by_slug")
    @patch.object(service, "view_owned")
    def test_create_with_existing_slug_becomes_update(self, view_owned, find_owned):
        find_owned.return_value = {"id": "generated-demo"}
        view_owned.return_value = {
            "id": "generated-demo",
            "name": "Demo",
            "description": "Description",
            "brief": "Use when testing",
            "provenance": {"slug": "demo"},
        }
        prepared = service._prepare_data(
            {
                "action": "create",
                "slug": "demo",
                "title": "Demo",
                "description": "Description",
                "brief": "Use when testing",
                "system_md": "# Demo",
            },
            "agent",
        )
        self.assertEqual(prepared["action"], "update")
        self.assertEqual(prepared["skill_id"], "generated-demo")

    @patch.object(service, "repository")
    @patch.object(service, "_store_candidate")
    @patch.object(service, "view_owned")
    def test_patch_requires_one_exact_match(self, view_owned, store_candidate, repository):
        view_owned.return_value = {
            "id": "generated-demo",
            "name": "Demo",
            "description": "Description",
            "brief": "Use when testing",
            "version": "0.1.0",
            "provenance": {"slug": "demo"},
            "system_md": "alpha beta",
        }
        store_candidate.return_value = {
            "id": "candidate",
            "action": "update",
            "title": "Demo",
            "status": "assigned",
            "skill_id": "generated-demo",
        }
        fake_repo = Mock()
        repository.return_value = fake_repo
        service.patch_from_tool(
            "agent",
            "session",
            {"skill_id": "generated-demo", "old_text": "alpha", "new_text": "gamma"},
            {},
            "agent:agent",
        )
        data = store_candidate.call_args.args[3]
        self.assertEqual(data["system_md"], "gamma beta")
        fake_repo.reset_tool_calls.assert_called_once_with("agent", "session")

        view_owned.return_value["system_md"] = "alpha alpha"
        with self.assertRaisesRegex(ValueError, "found 2"):
            service.patch_from_tool(
                "agent",
                "session",
                {"skill_id": "generated-demo", "old_text": "alpha", "new_text": "gamma"},
                {},
                "agent:agent",
            )


if __name__ == "__main__":
    unittest.main()
