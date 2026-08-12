import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS review_jobs(
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    agent_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS candidates(
    id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES review_jobs(id),
    agent_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    brief TEXT NOT NULL,
    system_md TEXT NOT NULL,
    transcript_json TEXT NOT NULL,
    risk_json TEXT NOT NULL,
    status TEXT NOT NULL,
    skill_id TEXT,
    rejection_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    reviewed_by TEXT,
    reviewed_at TEXT,
    materialized_at TEXT,
    action TEXT NOT NULL DEFAULT 'create'
);
CREATE TABLE IF NOT EXISTS session_state(
    agent_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    last_user_turns INTEGER NOT NULL DEFAULT 0,
    last_review_at TEXT,
    last_fingerprint TEXT,
    tool_calls_since_review INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(agent_id,session_id)
);
CREATE TABLE IF NOT EXISTS audit_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS candidate_status_idx ON candidates(status,updated_at);
CREATE INDEX IF NOT EXISTS audit_candidate_idx ON audit_log(candidate_id,created_at);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


class Repository:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)

    @staticmethod
    def _migrate(connection):
        candidate_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(candidates)")
        }
        if "action" not in candidate_columns:
            connection.execute(
                "ALTER TABLE candidates ADD COLUMN action TEXT NOT NULL DEFAULT 'create'"
            )
        state_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(session_state)")
        }
        if "tool_calls_since_review" not in state_columns:
            connection.execute(
                "ALTER TABLE session_state ADD COLUMN tool_calls_since_review INTEGER NOT NULL DEFAULT 0"
            )

    @contextmanager
    def connect(self, immediate=False):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            if immediate:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_job(self, key, agent_id, session_id):
        job_id, timestamp = str(uuid.uuid4()), now()
        with self.connect(True) as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO review_jobs"
                "(id,idempotency_key,agent_id,session_id,status,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (job_id, key, agent_id, session_id, "queued", timestamp),
            )
            if not cursor.rowcount:
                return None
        return job_id

    def set_job(self, job_id, status, error=None):
        timestamp = now()
        timestamp_field = {
            "running": "started_at",
            "completed": "completed_at",
            "failed": "completed_at",
        }.get(status)
        with self.connect(True) as connection:
            if timestamp_field:
                connection.execute(
                    f"UPDATE review_jobs SET status=?,error=?,{timestamp_field}=? WHERE id=?",
                    (status, error, timestamp, job_id),
                )
            else:
                connection.execute(
                    "UPDATE review_jobs SET status=?,error=? WHERE id=?",
                    (status, error, job_id),
                )

    def add_candidate(self, job_id, agent_id, session_id, data, risks, status, actor):
        candidate_id, timestamp = str(uuid.uuid4()), now()
        with self.connect(True) as connection:
            connection.execute(
                "INSERT INTO candidates"
                "(id,job_id,agent_id,session_id,slug,title,description,brief,system_md,"
                "transcript_json,risk_json,status,skill_id,created_at,updated_at,action) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    candidate_id,
                    job_id,
                    agent_id,
                    session_id,
                    data["slug"],
                    data["title"],
                    data["description"],
                    data["brief"],
                    data["system_md"],
                    "[]",
                    json.dumps(risks, ensure_ascii=False),
                    status,
                    data.get("skill_id"),
                    timestamp,
                    timestamp,
                    data.get("action", "create"),
                ),
            )
        self.audit(
            candidate_id,
            actor,
            "candidate_created",
            data.get("reason") or "reviewer produced a reusable procedure",
            {"status": status, "action": data.get("action", "create")},
        )
        return self.get_candidate(candidate_id)

    @staticmethod
    def _candidate(row):
        if not row:
            return None
        candidate = dict(row)
        candidate["risks"] = json.loads(candidate.pop("risk_json") or "[]")
        candidate.pop("transcript_json", None)
        return candidate

    def get_candidate(self, candidate_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM candidates WHERE id=?", (candidate_id,)
            ).fetchone()
        return self._candidate(row)

    def list_candidates(self, limit=100):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id,agent_id,session_id,slug,title,description,brief,status,"
                "skill_id,risk_json,created_at,updated_at,action "
                "FROM candidates ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._candidate(row) for row in rows]

    def pending_with_transcripts(self):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM candidates WHERE status='pending_review' ORDER BY created_at"
            ).fetchall()
        results = []
        for row in rows:
            raw = dict(row)
            transcript = json.loads(raw.get("transcript_json") or "[]")
            candidate = self._candidate(row)
            candidate["transcript"] = transcript
            results.append(candidate)
        return results

    def transition(self, candidate_id, allowed, status, actor, reason, **fields):
        with self.connect(True) as connection:
            row = connection.execute(
                "SELECT status FROM candidates WHERE id=?", (candidate_id,)
            ).fetchone()
            if not row:
                raise KeyError(candidate_id)
            if row["status"] not in allowed:
                raise ValueError(
                    f"candidate is {row['status']}, expected {', '.join(allowed)}"
                )
            updates = {"status": status, "updated_at": now(), **fields}
            sql = ",".join(f"{key}=?" for key in updates)
            connection.execute(
                f"UPDATE candidates SET {sql} WHERE id=?",
                (*updates.values(), candidate_id),
            )
        self.audit(candidate_id, actor, status, reason, fields)
        return self.get_candidate(candidate_id)

    def audit(self, candidate_id, actor, action, reason, details=None):
        with self.connect(True) as connection:
            connection.execute(
                "INSERT INTO audit_log"
                "(candidate_id,actor,action,reason,details_json,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    candidate_id,
                    actor,
                    action,
                    reason,
                    json.dumps(details or {}, ensure_ascii=False),
                    now(),
                ),
            )

    def audits(self, limit=200):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def add_tool_calls(self, agent_id, session_id, count, threshold):
        count, threshold = max(0, int(count)), max(1, int(threshold))
        with self.connect(True) as connection:
            row = connection.execute(
                "SELECT tool_calls_since_review FROM session_state "
                "WHERE agent_id=? AND session_id=?",
                (agent_id, session_id),
            ).fetchone()
            total = (row["tool_calls_since_review"] if row else 0) + count
            triggered = total >= threshold
            remaining = 0 if triggered else total
            connection.execute(
                "INSERT INTO session_state"
                "(agent_id,session_id,last_user_turns,last_review_at,last_fingerprint,tool_calls_since_review) "
                "VALUES(?,?,0,?,?,?) "
                "ON CONFLICT(agent_id,session_id) DO UPDATE SET "
                "tool_calls_since_review=excluded.tool_calls_since_review,"
                "last_review_at=CASE WHEN ? THEN excluded.last_review_at ELSE session_state.last_review_at END",
                (
                    agent_id,
                    session_id,
                    now() if triggered else None,
                    None,
                    remaining,
                    triggered,
                ),
            )
        return {"triggered": triggered, "count": remaining}

    def reset_tool_calls(self, agent_id, session_id):
        with self.connect(True) as connection:
            connection.execute(
                "INSERT INTO session_state"
                "(agent_id,session_id,last_user_turns,tool_calls_since_review) VALUES(?,?,0,0) "
                "ON CONFLICT(agent_id,session_id) DO UPDATE SET tool_calls_since_review=0",
                (agent_id, session_id),
            )

    def state(self, agent_id, session_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM session_state WHERE agent_id=? AND session_id=?",
                (agent_id, session_id),
            ).fetchone()
        return dict(row) if row else None

    def mark_reviewed(self, agent_id, session_id, fingerprint):
        with self.connect(True) as connection:
            connection.execute(
                "INSERT INTO session_state"
                "(agent_id,session_id,last_user_turns,last_review_at,last_fingerprint,tool_calls_since_review) "
                "VALUES(?,?,0,?,?,0) "
                "ON CONFLICT(agent_id,session_id) DO UPDATE SET "
                "last_review_at=excluded.last_review_at,last_fingerprint=excluded.last_fingerprint,"
                "tool_calls_since_review=0",
                (agent_id, session_id, now(), fingerprint),
            )

    def purge_transcripts(self):
        with self.connect(True) as connection:
            cursor = connection.execute(
                "UPDATE candidates SET transcript_json='[]' WHERE transcript_json<>'[]'"
            )
        return cursor.rowcount

    def stats(self):
        with self.connect() as connection:
            candidates = {
                row["status"]: row["count"]
                for row in connection.execute(
                    "SELECT status,COUNT(*) AS count FROM candidates GROUP BY status"
                )
            }
            jobs = {
                row["status"]: row["count"]
                for row in connection.execute(
                    "SELECT status,COUNT(*) AS count FROM review_jobs GROUP BY status"
                )
            }
            retained = connection.execute(
                "SELECT COALESCE(SUM(LENGTH(transcript_json)),0) FROM candidates"
            ).fetchone()[0]
            last_failed = connection.execute(
                "SELECT error,completed_at FROM review_jobs WHERE status='failed' "
                "ORDER BY completed_at DESC LIMIT 1"
            ).fetchone()
        return {
            "candidates": candidates,
            "jobs": jobs,
            "retained_transcript_chars": retained,
            "last_failure": dict(last_failed) if last_failed else None,
        }
