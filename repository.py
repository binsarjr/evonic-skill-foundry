import json, os, sqlite3, uuid
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS review_jobs(id TEXT PRIMARY KEY,idempotency_key TEXT NOT NULL UNIQUE,agent_id TEXT NOT NULL,session_id TEXT NOT NULL,status TEXT NOT NULL,error TEXT,created_at TEXT NOT NULL,started_at TEXT,completed_at TEXT);
CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY,job_id TEXT REFERENCES review_jobs(id),agent_id TEXT NOT NULL,session_id TEXT NOT NULL,slug TEXT NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL,brief TEXT NOT NULL,system_md TEXT NOT NULL,transcript_json TEXT NOT NULL,risk_json TEXT NOT NULL,status TEXT NOT NULL,skill_id TEXT,rejection_reason TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,reviewed_by TEXT,reviewed_at TEXT,materialized_at TEXT);
CREATE TABLE IF NOT EXISTS session_state(agent_id TEXT NOT NULL,session_id TEXT NOT NULL,last_user_turns INTEGER NOT NULL DEFAULT 0,last_review_at TEXT,last_fingerprint TEXT,PRIMARY KEY(agent_id,session_id));
CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT,candidate_id TEXT,actor TEXT NOT NULL,action TEXT NOT NULL,reason TEXT NOT NULL,details_json TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS candidate_status_idx ON candidates(status,updated_at);
CREATE INDEX IF NOT EXISTS audit_candidate_idx ON audit_log(candidate_id,created_at);
"""
def now(): return datetime.now(timezone.utc).isoformat()

class Repository:
    def __init__(self, path):
        self.path=path; os.makedirs(os.path.dirname(path),mode=0o700,exist_ok=True)
        with self.connect() as c: c.executescript(SCHEMA)
    @contextmanager
    def connect(self, immediate=False):
        c=sqlite3.connect(self.path,timeout=30); c.row_factory=sqlite3.Row; c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=30000'); c.execute('PRAGMA foreign_keys=ON')
        try:
            if immediate: c.execute('BEGIN IMMEDIATE')
            yield c; c.commit()
        except Exception: c.rollback(); raise
        finally: c.close()
    def create_job(self,key,agent_id,session_id):
        jid=str(uuid.uuid4()); ts=now()
        with self.connect(True) as c:
            cur=c.execute("INSERT OR IGNORE INTO review_jobs(id,idempotency_key,agent_id,session_id,status,created_at) VALUES(?,?,?,?,?,?)",(jid,key,agent_id,session_id,'queued',ts))
            if not cur.rowcount: return None
        return jid
    def set_job(self,jid,status,error=None):
        ts=now(); fields={"running":",started_at=?","completed":",completed_at=?","failed":",completed_at=?"}; extra=fields.get(status,'')
        with self.connect(True) as c: c.execute(f"UPDATE review_jobs SET status=?,error=?{extra} WHERE id=?",(status,error,*([ts] if extra else []),jid))
    def add_candidate(self,job_id,agent_id,session_id,data,transcript,risks,status):
        cid=str(uuid.uuid4()); ts=now()
        with self.connect(True) as c:
            c.execute("INSERT INTO candidates(id,job_id,agent_id,session_id,slug,title,description,brief,system_md,transcript_json,risk_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(cid,job_id,agent_id,session_id,data['slug'],data['title'],data['description'],data['brief'],data['system_md'],json.dumps(transcript,ensure_ascii=False),json.dumps(risks,ensure_ascii=False),status,ts,ts))
        self.audit(cid,'skill-foundry','candidate_created','reviewer produced a reusable procedure',{'status':status})
        return self.get_candidate(cid)
    def get_candidate(self,cid,include_transcript=True):
        with self.connect() as c: row=c.execute('SELECT * FROM candidates WHERE id=?',(cid,)).fetchone()
        if not row:return None
        d=dict(row); d['risks']=json.loads(d.pop('risk_json') or '[]')
        transcript=json.loads(d.pop('transcript_json') or '[]'); d['transcript']=transcript if include_transcript else []
        return d
    def list_candidates(self,limit=100):
        with self.connect() as c: rows=c.execute('SELECT id,agent_id,session_id,slug,title,description,brief,status,skill_id,risk_json,created_at,updated_at FROM candidates ORDER BY updated_at DESC LIMIT ?', (limit,)).fetchall()
        out=[]
        for r in rows:
            d=dict(r); d['risks']=json.loads(d.pop('risk_json') or '[]'); out.append(d)
        return out
    def transition(self,cid,allowed,status,actor,reason,**fields):
        with self.connect(True) as c:
            row=c.execute('SELECT status FROM candidates WHERE id=?',(cid,)).fetchone()
            if not row: raise KeyError(cid)
            if row['status'] not in allowed: raise ValueError(f"candidate is {row['status']}, expected {', '.join(allowed)}")
            updates={'status':status,'updated_at':now(),**fields}; sql=','.join(f'{k}=?' for k in updates)
            c.execute(f'UPDATE candidates SET {sql} WHERE id=?',(*updates.values(),cid))
        self.audit(cid,actor,status,reason,fields); return self.get_candidate(cid)
    def audit(self,cid,actor,action,reason,details=None):
        with self.connect(True) as c: c.execute('INSERT INTO audit_log(candidate_id,actor,action,reason,details_json,created_at) VALUES(?,?,?,?,?,?)',(cid,actor,action,reason,json.dumps(details or {},ensure_ascii=False),now()))
    def audits(self,limit=200):
        with self.connect() as c: rows=c.execute('SELECT * FROM audit_log ORDER BY id DESC LIMIT ?',(limit,)).fetchall()
        return [dict(r) for r in rows]
    def state(self,agent_id,session_id):
        with self.connect() as c: r=c.execute('SELECT * FROM session_state WHERE agent_id=? AND session_id=?',(agent_id,session_id)).fetchone()
        return dict(r) if r else None
    def save_state(self,agent_id,session_id,user_turns,fingerprint):
        with self.connect(True) as c: c.execute("INSERT INTO session_state(agent_id,session_id,last_user_turns,last_review_at,last_fingerprint) VALUES(?,?,?,?,?) ON CONFLICT(agent_id,session_id) DO UPDATE SET last_user_turns=excluded.last_user_turns,last_review_at=excluded.last_review_at,last_fingerprint=excluded.last_fingerprint",(agent_id,session_id,user_turns,now(),fingerprint))
