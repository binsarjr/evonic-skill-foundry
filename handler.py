from concurrent.futures import ThreadPoolExecutor
from .service import config_errors, review
_executor=None

def on_enable(sdk=None):
    global _executor
    if _executor is None:_executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='skill-foundry')

def on_disable():
    global _executor
    if _executor:_executor.shutdown(wait=False,cancel_futures=True); _executor=None

def on_turn_complete(ev,sdk):
    config=sdk.config or {}
    if not config.get('AUTO_REVIEW_ENABLED',False):return
    agent_id,session_id=ev.get('agent_id',''),ev.get('session_id','')
    if not agent_id or not session_id:return
    try:
        from backend.plugin_manager import plugin_manager
        settings=plugin_manager.get_agent_plugin_settings('skill_foundry',agent_id)
        if not settings.get('enabled',False):return
        errors=config_errors(config)
        if errors:sdk.log('; '.join(errors),'error'); return
        limit=min(500,max(10,int(config.get('TRANSCRIPT_MESSAGE_LIMIT',100))))
        messages=sdk.get_session_messages(session_id,agent_id,limit=limit)
        def run():
            try: review(agent_id,session_id,messages,config)
            except Exception as e: sdk.log(f'Review failed for session {session_id}: {e}','error')
        if _executor:_executor.submit(run)
    except Exception as e:sdk.log(f'Unable to queue review: {e}','error')
