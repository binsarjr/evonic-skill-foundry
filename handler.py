from concurrent.futures import ThreadPoolExecutor

from .service import config_errors, repository, review


TOOL_ID = "plugin:skill_foundry:skill_foundry_manage"
TOOL_NAME = "skill_foundry_manage"
_executor = None


def _agent_enabled(agent_id):
    from models.db import db

    stored = db.get_setting(f"plugin_agent_setting:skill_foundry:{agent_id}:enabled")
    return stored is None or stored in ("1", "true", "True")


def _sync_agent_tool(agent_id, enabled):
    from models.db import db

    tools = db.get_agent_tools(agent_id)
    changed = False
    if enabled and TOOL_ID not in tools:
        tools.append(TOOL_ID)
        changed = True
    if not enabled and TOOL_ID in tools:
        tools = [tool for tool in tools if tool != TOOL_ID]
        changed = True
    if changed:
        db.set_agent_tools(agent_id, tools)


def _sync_all_tools(enabled=True):
    from models.db import db

    for agent in db.get_agents():
        _sync_agent_tool(agent["id"], enabled and _agent_enabled(agent["id"]))


def on_enable(sdk=None):
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="skill-foundry")
    _sync_all_tools(True)


def on_disable():
    global _executor
    _sync_all_tools(False)
    if _executor:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None


def _notify(session_id, candidate):
    from backend.agent_runtime import agent_runtime

    action = "created" if candidate.get("action") == "create" else "updated"
    if candidate.get("status") == "pending_review":
        text = f"Skill Foundry prepared a {action} candidate: {candidate['title']} (approval required)."
    else:
        text = f"Skill Foundry {action} skill: {candidate['title']} ({candidate.get('skill_id')})."
    agent_runtime.send_as_bot(session_id, text)


def on_turn_complete(event, sdk):
    config = sdk.config or {}
    agent_id, session_id = event.get("agent_id", ""), event.get("session_id", "")
    if not agent_id or not session_id:
        return
    try:
        enabled = _agent_enabled(agent_id)
        _sync_agent_tool(agent_id, enabled)
        if not enabled or not config.get("AUTO_REVIEW_ENABLED", True):
            return
        if event.get("is_error") or event.get("slash_command"):
            return
        errors = config_errors(config)
        if errors:
            sdk.log("; ".join(errors), "error")
            return

        trace = event.get("tool_trace") or []
        names = [entry.get("tool") for entry in trace if isinstance(entry, dict)]
        if TOOL_NAME in names:
            repository().reset_tool_calls(agent_id, session_id)
            return
        count = sum(1 for name in names if name)
        if not count:
            return
        threshold = max(1, int(config.get("REVIEW_EVERY_TOOL_CALLS", 10)))
        state = repository().add_tool_calls(agent_id, session_id, count, threshold)
        if not state["triggered"]:
            return

        limit = min(500, max(10, int(config.get("TRANSCRIPT_MESSAGE_LIMIT", 100))))
        messages = sdk.get_session_messages(session_id, agent_id, limit=limit)

        def run():
            try:
                candidate = review(agent_id, session_id, messages, config)
                if candidate:
                    _notify(session_id, candidate)
            except Exception as error:
                sdk.log(f"Review failed for session {session_id}: {error}", "error")

        if _executor:
            _executor.submit(run)
    except Exception as error:
        sdk.log(f"Unable to queue review: {error}", "error")
