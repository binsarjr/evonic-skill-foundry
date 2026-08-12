import hashlib
import json
import os

from .materializer import (
    assign,
    enable,
    find_owned_by_slug,
    list_owned,
    materialize,
    view_owned,
)
from .repository import Repository, now
from .reviewer import SUBMIT_TOOL, SYSTEM, extract_result, transcript_prompt
from .validator import validate_candidate


_repo = None


def repository():
    global _repo
    if _repo is None:
        from backend.plugin_sdk import PLUGIN_DB_DIR

        _repo = Repository(os.path.join(PLUGIN_DB_DIR, "skill_foundry.db"))
    return _repo


def config_errors(config):
    if config.get("AUTO_ASSIGN_GENERATED_SKILLS") and not config.get(
        "AUTO_ENABLE_GENERATED_SKILLS"
    ):
        return ["AUTO_ASSIGN_GENERATED_SKILLS requires AUTO_ENABLE_GENERATED_SKILLS"]
    return []


def _client(agent_id, config):
    from backend.llm_client import LLMClient, get_llm_client
    from models.db import db

    model_id = str(config.get("REVIEW_MODEL_ID") or "").strip()
    agent = db.get_agent(agent_id) or {}
    model_id = model_id or agent.get("model_id") or agent.get("default_model_id") or ""
    model = db.get_model_by_id(model_id) if model_id else None
    client = LLMClient(model_config=model) if model else get_llm_client()
    client.max_retries = max(0, int(config.get("MAX_RETRIES", 2)))
    return client


def _fingerprint(messages):
    serialized = json.dumps(messages, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(serialized.encode()).hexdigest()


def _request_review(client, messages, skills, config):
    request = {
        "messages": [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": transcript_prompt(messages, skills),
            },
        ],
        "tools": [SUBMIT_TOOL],
        "temperature": 0.2,
        "enable_thinking": False,
        "max_tokens": max(512, int(config.get("MAX_REVIEW_TOKENS", 4096))),
    }
    result = client.chat_completion(
        **request, tool_choice="submit_skill_review"
    )
    error = str(result.get("error_detail") or result.get("error_type") or "")
    if not result.get("success") and "tool_choice" in error.lower().replace(" ", "_"):
        result = client.chat_completion(**request)
    return result


def _prepare_data(data, agent_id):
    prepared = dict(data)
    for key in ("skill_id", "slug", "title", "description", "brief", "system_md", "reason"):
        if key in prepared and isinstance(prepared[key], str):
            prepared[key] = prepared[key].strip()

    if prepared.get("action") == "create":
        existing = find_owned_by_slug(prepared.get("slug", ""), agent_id)
        if existing:
            prepared["action"] = "update"
            prepared["skill_id"] = existing["id"]

    if prepared.get("action") == "update":
        current = view_owned(prepared.get("skill_id", ""), agent_id)
        provenance = current.get("provenance") or {}
        prepared["slug"] = prepared.get("slug") or provenance.get("slug") or current[
            "id"
        ].removeprefix("generated-")
        prepared["title"] = prepared.get("title") or current["name"]
        prepared["description"] = prepared.get("description") or current["description"]
        prepared["brief"] = prepared.get("brief") or current["brief"]
    return prepared


def _store_candidate(job_id, agent_id, session_id, data, config, actor):
    data = _prepare_data(data, agent_id)
    validation, risks = validate_candidate(data)
    if validation:
        raise ValueError("; ".join(validation))
    status = "pending_review" if config.get("REQUIRE_APPROVAL", False) else "approved"
    candidate = repository().add_candidate(
        job_id, agent_id, session_id, data, risks, status, actor
    )
    if status == "approved":
        candidate = materialize_candidate(
            candidate["id"], config, actor, "approval disabled"
        )
    return candidate


def review(agent_id, session_id, messages, config, force=False, actor="skill-foundry"):
    repo = repository()
    errors = config_errors(config)
    if errors:
        raise ValueError("; ".join(errors))

    fingerprint = _fingerprint(messages)
    key = hashlib.sha256(f"{agent_id}:{session_id}:{fingerprint}:v2".encode()).hexdigest()
    job_id = repo.create_job(key, agent_id, session_id)
    if not job_id:
        return None
    repo.set_job(job_id, "running")
    try:
        result = _request_review(
            _client(agent_id, config),
            messages,
            list_owned(agent_id, include_content=True),
            config,
        )
        if not result.get("success"):
            raise RuntimeError(
                result.get("error_detail")
                or result.get("error_type")
                or "LLM review failed"
            )
        data = extract_result(result)
        repo.mark_reviewed(agent_id, session_id, fingerprint)
        if data["action"] == "none":
            repo.audit(
                None,
                actor,
                "review_noop",
                data["reason"],
                {"job_id": job_id},
            )
            repo.set_job(job_id, "completed")
            return None
        candidate = _store_candidate(
            job_id, agent_id, session_id, data, config, actor
        )
        repo.set_job(job_id, "completed")
        return candidate
    except Exception as error:
        repo.set_job(job_id, "failed", str(error))
        raise


def materialize_candidate(candidate_id, config, actor, reason):
    repo = repository()
    candidate = repo.get_candidate(candidate_id)
    if not candidate:
        raise KeyError(candidate_id)
    errors = config_errors(config)
    if errors:
        raise ValueError("; ".join(errors))
    if candidate["status"] == "pending_review":
        candidate = repo.transition(
            candidate_id,
            ["pending_review"],
            "approved",
            actor,
            reason,
            reviewed_by=actor,
            reviewed_at=now(),
        )
    if candidate["status"] == "approved":
        skill_id = materialize(candidate)
        candidate = repo.transition(
            candidate_id,
            ["approved"],
            "materialized",
            actor,
            reason,
            skill_id=skill_id,
            materialized_at=now(),
        )
    if config.get("AUTO_ENABLE_GENERATED_SKILLS") and candidate["status"] == "materialized":
        enable(candidate["skill_id"])
        candidate = repo.transition(
            candidate_id,
            ["materialized"],
            "enabled",
            actor,
            "automatic enablement",
        )
    if config.get("AUTO_ASSIGN_GENERATED_SKILLS"):
        if not config.get("AUTO_ENABLE_GENERATED_SKILLS"):
            raise ValueError(config_errors(config)[0])
        if candidate["status"] == "enabled":
            assign(candidate["skill_id"], candidate["agent_id"])
            candidate = repo.transition(
                candidate_id,
                ["enabled"],
                "assigned",
                actor,
                "automatic source-agent assignment",
            )
    return candidate


def create_from_tool(agent_id, session_id, arguments, config, actor):
    data = {
        "action": "create",
        "reason": "agent explicitly saved a reusable procedure",
        **{
            key: arguments.get(key, "")
            for key in ("slug", "title", "description", "brief", "system_md")
        },
    }
    candidate = _store_candidate(None, agent_id, session_id, data, config, actor)
    repository().reset_tool_calls(agent_id, session_id)
    return candidate


def patch_from_tool(agent_id, session_id, arguments, config, actor):
    skill = view_owned(str(arguments.get("skill_id") or "").strip(), agent_id)
    old_text = arguments.get("old_text")
    new_text = arguments.get("new_text")
    if not isinstance(old_text, str) or not old_text:
        raise ValueError("old_text is required for patch")
    if not isinstance(new_text, str):
        raise ValueError("new_text is required for patch")
    matches = skill["system_md"].count(old_text)
    if matches != 1:
        raise ValueError(f"old_text must match exactly once; found {matches}")
    provenance = skill.get("provenance") or {}
    data = {
        "action": "update",
        "reason": "agent patched an existing reusable procedure",
        "skill_id": skill["id"],
        "slug": provenance.get("slug") or skill["id"].removeprefix("generated-"),
        "title": skill["name"],
        "description": skill["description"],
        "brief": skill["brief"],
        "system_md": skill["system_md"].replace(old_text, new_text, 1),
    }
    candidate = _store_candidate(None, agent_id, session_id, data, config, actor)
    repository().reset_tool_calls(agent_id, session_id)
    return candidate


def list_owned_skills(agent_id):
    return list_owned(agent_id)


def view_owned_skill(agent_id, skill_id):
    return view_owned(skill_id, agent_id)


def re_review_pending(config, actor="migration:v0.2.0"):
    repo = repository()
    pending = repo.pending_with_transcripts()
    results = []
    for old in pending:
        candidate = review(
            old["agent_id"],
            old["session_id"],
            old.pop("transcript"),
            config,
            force=True,
            actor=actor,
        )
        repo.transition(
            old["id"],
            ["pending_review"],
            "rejected",
            actor,
            "superseded by v0.2.0 re-review",
            rejection_reason="superseded by v0.2.0 re-review",
            reviewed_by=actor,
            reviewed_at=now(),
        )
        results.append(
            {
                "old_candidate_id": old["id"],
                "result": None
                if candidate is None
                else {
                    "candidate_id": candidate["id"],
                    "action": candidate["action"],
                    "skill_id": candidate.get("skill_id"),
                    "status": candidate["status"],
                },
            }
        )
    purged = repo.purge_transcripts()
    return {"reviewed": len(pending), "purged": purged, "results": results}


def approve(candidate_id, config, actor, reason):
    return materialize_candidate(candidate_id, config, actor, reason)


def reject(candidate_id, actor, reason):
    return repository().transition(
        candidate_id,
        ["pending_review", "approved"],
        "rejected",
        actor,
        reason,
        rejection_reason=reason,
        reviewed_by=actor,
        reviewed_at=now(),
    )


def set_enabled(candidate_id, actor, enabled_value):
    repo = repository()
    candidate = repo.get_candidate(candidate_id)
    if not candidate:
        raise KeyError(candidate_id)
    enable(candidate["skill_id"], enabled_value)
    status = "enabled" if enabled_value else "disabled"
    return repo.transition(
        candidate_id,
        ["materialized", "enabled", "assigned", "disabled"],
        status,
        actor,
        "dashboard skill toggle",
    )


def set_assigned(candidate_id, actor, assigned_value, agent_id=None):
    repo = repository()
    candidate = repo.get_candidate(candidate_id)
    if not candidate:
        raise KeyError(candidate_id)
    target = agent_id or candidate["agent_id"]
    if assigned_value:
        from backend.skills_manager import skills_manager

        if not skills_manager.is_skill_enabled(candidate["skill_id"]):
            raise ValueError("skill must be enabled before assignment")
    assign(candidate["skill_id"], target, assigned_value)
    status = "assigned" if assigned_value else "enabled"
    return repo.transition(
        candidate_id,
        ["enabled", "assigned"],
        status,
        actor,
        "dashboard assignment change",
    )
