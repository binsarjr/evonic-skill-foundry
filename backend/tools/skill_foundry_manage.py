import sys


def _service():
    matches = [
        module
        for name, module in tuple(sys.modules.items())
        if name.startswith("plugin_pkg_skill_foundry_") and name.endswith(".service")
    ]
    if not matches:
        raise RuntimeError("Skill Foundry service is not loaded")
    return matches[-1]


def _result(candidate):
    return {
        "status": "ok",
        "action": candidate["action"],
        "candidate_id": candidate["id"],
        "skill_id": candidate.get("skill_id"),
        "title": candidate["title"],
        "state": candidate["status"],
        "available_next_turn": candidate["status"] in {"enabled", "assigned"},
    }


def execute(agent, args):
    from backend.plugin_manager import plugin_manager

    agent_id = agent.get("id") or agent.get("agent_id") or ""
    session_id = agent.get("session_id") or ""
    if not agent_id or not session_id:
        return {"error": "agent_id and session_id are required in tool context"}
    settings = plugin_manager.get_agent_plugin_settings("skill_foundry", agent_id)
    if not settings.get("enabled", True):
        return {
            "error": "Skill Foundry self-improvement is disabled for this agent.",
            "blocked_by": "agent_opt_out",
        }

    action = str(args.get("action") or "").strip().lower()
    service = _service()
    config = plugin_manager.get_plugin_config("skill_foundry")
    actor = f"agent:{agent_id}"
    try:
        if action == "list":
            return {
                "status": "ok",
                "action": "list",
                "skills": service.list_owned_skills(agent_id),
            }
        if action == "view":
            skill_id = str(args.get("skill_id") or "").strip()
            if not skill_id:
                raise ValueError("skill_id is required for view")
            return {
                "status": "ok",
                "action": "view",
                "skill": service.view_owned_skill(agent_id, skill_id),
            }
        if action == "create":
            return _result(
                service.create_from_tool(
                    agent_id, session_id, args, config, actor
                )
            )
        if action == "patch":
            return _result(
                service.patch_from_tool(
                    agent_id, session_id, args, config, actor
                )
            )
        return {"error": "action must be list, view, create, or patch"}
    except (KeyError, ValueError, RuntimeError) as error:
        return {"error": str(error)}
