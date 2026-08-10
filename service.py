import hashlib, json, os, threading
from datetime import datetime, timezone
from .materializer import assign, enable, materialize
from .repository import Repository, now
from .reviewer import SYSTEM, extract_reply, parse_result, transcript_prompt
from .validator import validate_candidate

_repo=None; _lock=threading.Lock()
def repository():
    global _repo
    if _repo is None:
        from backend.plugin_sdk import PLUGIN_DB_DIR
        _repo=Repository(os.path.join(PLUGIN_DB_DIR,'skill_foundry.db'))
    return _repo
def config_errors(config):
    return ['AUTO_ASSIGN_GENERATED_SKILLS requires AUTO_ENABLE_GENERATED_SKILLS'] if config.get('AUTO_ASSIGN_GENERATED_SKILLS') and not config.get('AUTO_ENABLE_GENERATED_SKILLS') else []
def eligible(repo,agent_id,session_id,messages,config,force=False):
    users=sum(1 for m in messages if m.get('role')=='user'); minimum=max(1,int(config.get('MIN_USER_TURNS',5))); interval=max(1,int(config.get('REVIEW_EVERY_USER_TURNS',5)))
    fp=hashlib.sha256(json.dumps(messages,sort_keys=True,default=str).encode()).hexdigest(); state=repo.state(agent_id,session_id)
    if force:return True,users,fp
    if users<minimum:return False,users,fp
    if state and (users-state['last_user_turns']<interval or state['last_fingerprint']==fp):return False,users,fp
    if state and state.get('last_review_at'):
        elapsed=(datetime.now(timezone.utc)-datetime.fromisoformat(state['last_review_at'])).total_seconds()
        if elapsed<float(config.get('REVIEW_COOLDOWN_SECONDS',300)):return False,users,fp
    return True,users,fp
def _client(agent_id,config):
    from backend.llm_client import LLMClient, get_llm_client
    from models.db import db
    model_id=str(config.get('REVIEW_MODEL_ID') or '').strip(); agent=db.get_agent(agent_id) or {}
    model_id=model_id or agent.get('model_id') or agent.get('default_model_id') or ''
    model=db.get_model_by_id(model_id) if model_id else None
    client=LLMClient(model_config=model) if model else get_llm_client(); client.max_retries=max(0,int(config.get('MAX_RETRIES',2))); return client
def review(agent_id,session_id,messages,config,force=False,actor='skill-foundry'):
    repo=repository(); errors=config_errors(config)
    if errors: raise ValueError('; '.join(errors))
    ok,users,fp=eligible(repo,agent_id,session_id,messages,config,force)
    if not ok:return None
    key=hashlib.sha256(f'{agent_id}:{session_id}:{fp}:v1'.encode()).hexdigest(); jid=repo.create_job(key,agent_id,session_id)
    if not jid:return None
    repo.set_job(jid,'running')
    try:
        result=_client(agent_id,config).chat_completion(messages=[{'role':'system','content':SYSTEM},{'role':'user','content':transcript_prompt(messages)}],temperature=0.2,enable_thinking=False,max_tokens=max(512,int(config.get('MAX_REVIEW_TOKENS',4096))))
        if not result.get('success'): raise RuntimeError(result.get('error_detail') or result.get('error_type') or 'LLM review failed')
        data=parse_result(extract_reply(result)); repo.save_state(agent_id,session_id,users,fp)
        if data['action']=='none': repo.audit(None,actor,'review_noop',data.get('reason','no reusable procedure'),{'job_id':jid}); repo.set_job(jid,'completed'); return None
        validation,risks=validate_candidate(data)
        if validation: raise ValueError('; '.join(validation))
        status='pending_review' if config.get('REQUIRE_APPROVAL',True) else 'approved'
        candidate=repo.add_candidate(jid,agent_id,session_id,data,messages,risks,status)
        if status=='approved': candidate=materialize_candidate(candidate['id'],config,actor,'approval disabled')
        repo.set_job(jid,'completed'); return candidate
    except Exception as e: repo.set_job(jid,'failed',str(e)); raise
def materialize_candidate(cid,config,actor,reason):
    repo=repository(); candidate=repo.get_candidate(cid)
    if not candidate: raise KeyError(cid)
    errors=config_errors(config)
    if errors: raise ValueError('; '.join(errors))
    if candidate['status']=='pending_review': candidate=repo.transition(cid,['pending_review'],'approved',actor,reason,reviewed_by=actor,reviewed_at=now())
    if candidate['status']=='approved':
        skill_id=materialize(candidate); candidate=repo.transition(cid,['approved'],'materialized',actor,reason,skill_id=skill_id,materialized_at=now())
    if config.get('AUTO_ENABLE_GENERATED_SKILLS') and candidate['status']=='materialized':
        enable(candidate['skill_id']); candidate=repo.transition(cid,['materialized'],'enabled',actor,'automatic enablement')
    if config.get('AUTO_ASSIGN_GENERATED_SKILLS'):
        if not config.get('AUTO_ENABLE_GENERATED_SKILLS'): raise ValueError(config_errors(config)[0])
        if candidate['status']=='enabled': assign(candidate['skill_id'],candidate['agent_id']); candidate=repo.transition(cid,['enabled'],'assigned',actor,'automatic source-agent assignment')
    return candidate
def approve(cid,config,actor,reason): return materialize_candidate(cid,config,actor,reason)
def reject(cid,actor,reason): return repository().transition(cid,['pending_review','approved'],'rejected',actor,reason,rejection_reason=reason,reviewed_by=actor,reviewed_at=now())
def set_enabled(cid,actor,enabled_value):
    repo=repository(); c=repo.get_candidate(cid); enable(c['skill_id'],enabled_value)
    status='enabled' if enabled_value else 'disabled'; return repo.transition(cid,['materialized','enabled','assigned','disabled'],status,actor,'dashboard skill toggle')
def set_assigned(cid,actor,assigned_value,agent_id=None):
    repo=repository(); c=repo.get_candidate(cid); target=agent_id or c['agent_id']
    if assigned_value:
        from backend.skills_manager import skills_manager
        if not skills_manager.is_skill_enabled(c['skill_id']): raise ValueError('skill must be enabled before assignment')
    assign(c['skill_id'],target,assigned_value); status='assigned' if assigned_value else ('enabled' if assigned_value is False else c['status'])
    return repo.transition(cid,['enabled','assigned'],status,actor,'dashboard assignment change')
