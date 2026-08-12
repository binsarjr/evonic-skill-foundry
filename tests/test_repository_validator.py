import os
import sqlite3
import tempfile
import unittest

from _package import load


repository_module = load("repository")
reviewer = load("reviewer")
validator = load("validator")
Repository = repository_module.Repository


CANDIDATE = {
    "action": "create",
    "reason": "reusable",
    "slug": "safe-procedure",
    "title": "Safe procedure",
    "description": "A sufficiently descriptive generated procedure",
    "brief": "Use when a repeatable procedure applies",
    "system_md": "# Safe procedure\n\nFollow the verified steps.",
}


class RepositoryTests(unittest.TestCase):
    def test_idempotent_job_and_no_transcript_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(os.path.join(directory, "db.sqlite"))
            job_id = repo.create_job("key", "agent", "session")
            self.assertTrue(job_id)
            self.assertIsNone(repo.create_job("key", "agent", "session"))
            candidate = repo.add_candidate(
                job_id, "agent", "session", CANDIDATE, [], "approved", "test"
            )
            self.assertNotIn("transcript", candidate)
            with repo.connect() as connection:
                stored = connection.execute(
                    "SELECT transcript_json FROM candidates WHERE id=?", (candidate["id"],)
                ).fetchone()[0]
            self.assertEqual(stored, "[]")

    def test_tool_counter_triggers_once_and_resets(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repository(os.path.join(directory, "db.sqlite"))
            self.assertEqual(
                repo.add_tool_calls("agent", "session", 4, 10),
                {"triggered": False, "count": 4},
            )
            self.assertEqual(
                repo.add_tool_calls("agent", "session", 6, 10),
                {"triggered": True, "count": 0},
            )
            self.assertEqual(
                repo.add_tool_calls("agent", "session", 1, 10),
                {"triggered": False, "count": 1},
            )
            repo.reset_tool_calls("agent", "session")
            self.assertEqual(repo.state("agent", "session")["tool_calls_since_review"], 0)

    def test_migrates_v1_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "db.sqlite")
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE review_jobs(id TEXT PRIMARY KEY,idempotency_key TEXT UNIQUE,agent_id TEXT,session_id TEXT,status TEXT,error TEXT,created_at TEXT,started_at TEXT,completed_at TEXT);
                CREATE TABLE candidates(id TEXT PRIMARY KEY,job_id TEXT,agent_id TEXT,session_id TEXT,slug TEXT,title TEXT,description TEXT,brief TEXT,system_md TEXT,transcript_json TEXT,risk_json TEXT,status TEXT,skill_id TEXT,rejection_reason TEXT,created_at TEXT,updated_at TEXT,reviewed_by TEXT,reviewed_at TEXT,materialized_at TEXT);
                CREATE TABLE session_state(agent_id TEXT,session_id TEXT,last_user_turns INTEGER DEFAULT 0,last_review_at TEXT,last_fingerprint TEXT,PRIMARY KEY(agent_id,session_id));
                CREATE TABLE audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT,candidate_id TEXT,actor TEXT,action TEXT,reason TEXT,details_json TEXT,created_at TEXT);
                """
            )
            connection.close()
            repo = Repository(path)
            with repo.connect() as migrated:
                candidate_columns = {row[1] for row in migrated.execute("PRAGMA table_info(candidates)")}
                state_columns = {row[1] for row in migrated.execute("PRAGMA table_info(session_state)")}
            self.assertIn("action", candidate_columns)
            self.assertIn("tool_calls_since_review", state_columns)


class ValidationAndReviewerTests(unittest.TestCase):
    def test_validator_blocks_secret(self):
        data = dict(CANDIDATE, system_md="Use token ghp_abcdefghijklmnopqrstuvwxyz1234567890")
        errors, risks = validator.validate_candidate(data)
        self.assertTrue(errors)
        self.assertTrue(any(item["severity"] == "critical" for item in risks))

    def test_reviewer_parses_forced_tool_and_json_fallback(self):
        result = {
            "response": {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "submit_skill_review",
                                        "arguments": '{"action":"none","reason":"one-off"}',
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        }
        self.assertEqual(reviewer.extract_result(result)["action"], "none")
        self.assertEqual(
            reviewer.parse_result('```json\n{"action":"none","reason":"one-off"}\n```')[
                "action"
            ],
            "none",
        )


if __name__ == "__main__":
    unittest.main()
