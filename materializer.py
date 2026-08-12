import json
import os
import re
import shutil
import tempfile


PLUGIN_ID = "skill_foundry"


def _base_id(slug):
    normalized = re.sub(r"[^a-z0-9_-]+", "-", slug.lower()).strip("-_")
    return "generated-" + normalized


def _owned(manifest, agent_id):
    provenance = manifest.get("provenance") or {}
    return (
        provenance.get("plugin") == PLUGIN_ID
        and provenance.get("agent_id") == agent_id
    )


def _clean_manifest(manifest):
    clean = dict(manifest)
    for key in ("_dir", "enabled", "protected", "tools", "tool_count", "config", "_setup_result"):
        clean.pop(key, None)
    return clean


def _read_system(skill_dir):
    path = os.path.join(skill_dir, "SYSTEM.md")
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _write_package(path, manifest, system_md):
    with open(os.path.join(path, "skill.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    with open(os.path.join(path, "SYSTEM.md"), "w", encoding="utf-8") as handle:
        handle.write(system_md.strip() + "\n")


def _next_version(version):
    match = re.fullmatch(r"0\.(\d+)\.(\d+)", str(version or ""))
    if not match:
        return "0.1.1"
    return f"0.{int(match.group(1))}.{int(match.group(2)) + 1}"


def unique_id(slug):
    from backend.skills_manager import SKILLS_DIR

    base = _base_id(slug)
    candidate, suffix = base, 2
    while os.path.exists(os.path.join(SKILLS_DIR, candidate)):
        candidate = f"{base}-v{suffix}"
        suffix += 1
    return candidate


def find_owned_by_slug(slug, agent_id):
    from backend.skills_manager import skills_manager

    for skill in skills_manager.list_skills():
        provenance = skill.get("provenance") or {}
        if _owned(skill, agent_id) and provenance.get("slug") == slug:
            return skill
    return None


def list_owned(agent_id, include_content=False, content_budget=40000):
    from backend.skills_manager import skills_manager

    result = []
    remaining = max(0, int(content_budget))
    for skill in skills_manager.list_skills():
        if not _owned(skill, agent_id):
            continue
        item = {
            "id": skill["id"],
            "name": skill.get("name", skill["id"]),
            "version": skill.get("version", ""),
            "description": skill.get("description", ""),
            "brief": skill.get("brief", ""),
            "enabled": bool(skill.get("enabled")),
        }
        if include_content and remaining:
            content = _read_system(skill.get("_dir", ""))[:remaining]
            item["system_md"] = content
            remaining -= len(content)
        result.append(item)
    return sorted(result, key=lambda item: item["id"])[:50]


def view_owned(skill_id, agent_id):
    from backend.skills_manager import skills_manager

    skill = skills_manager.get_skill(skill_id)
    if not skill or not _owned(skill, agent_id):
        raise ValueError("skill is not owned by Skill Foundry for this agent")
    result = {
        "id": skill["id"],
        "name": skill.get("name", skill["id"]),
        "version": skill.get("version", ""),
        "description": skill.get("description", ""),
        "brief": skill.get("brief", ""),
        "enabled": bool(skill.get("enabled")),
        "provenance": skill.get("provenance") or {},
        "system_md": _read_system(skill["_dir"]),
    }
    return result


def _new_manifest(candidate, skill_id):
    return {
        "id": skill_id,
        "name": candidate["title"],
        "version": "0.1.0",
        "description": candidate["description"],
        "brief": candidate["brief"],
        "author": "Skill Foundry",
        "default_enabled": False,
        "lazy_tools": True,
        "variables": [],
        "generated": True,
        "provenance": {
            "plugin": PLUGIN_ID,
            "slug": candidate["slug"],
            "candidate_id": candidate["id"],
            "agent_id": candidate["agent_id"],
            "session_id": candidate["session_id"],
        },
    }


def _updated_manifest(current, candidate):
    manifest = _clean_manifest(current)
    provenance = dict(manifest.get("provenance") or {})
    provenance.update(
        {
            "plugin": PLUGIN_ID,
            "slug": candidate["slug"],
            "last_candidate_id": candidate["id"],
            "agent_id": candidate["agent_id"],
            "session_id": candidate["session_id"],
        }
    )
    manifest.update(
        {
            "name": candidate["title"],
            "version": _next_version(manifest.get("version")),
            "description": candidate["description"],
            "brief": candidate["brief"],
            "provenance": provenance,
        }
    )
    return manifest


def _create(candidate):
    from backend.skills_manager import skills_manager
    from models.db import db

    skill_id = unique_id(candidate["slug"])
    stage = tempfile.mkdtemp(prefix=f"skill-foundry-{skill_id}-")
    try:
        _write_package(stage, _new_manifest(candidate, skill_id), candidate["system_md"])
        result = skills_manager.install_skill_from_dir(stage)
        if result.get("error"):
            raise RuntimeError(result["error"])
        disabled = skills_manager.set_skill_enabled(skill_id, False)
        if disabled.get("error"):
            raise RuntimeError(disabled["error"])
        for agent in db.get_agents():
            assigned = db.get_agent_skills(agent["id"])
            if skill_id in assigned:
                db.set_agent_skills(agent["id"], [item for item in assigned if item != skill_id])
        return skill_id
    except Exception:
        installed = skills_manager.get_skill(skill_id)
        if installed and _owned(installed, candidate["agent_id"]):
            skills_manager.uninstall_skill(skill_id)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _update(candidate):
    from backend.skills_manager import skills_manager

    skill_id = candidate.get("skill_id") or ""
    current = skills_manager.get_skill(skill_id)
    if not current or not _owned(current, candidate["agent_id"]):
        raise ValueError("target skill is not owned by Skill Foundry for this agent")

    stage = tempfile.mkdtemp(prefix=f"skill-foundry-{skill_id}-stage-")
    backup = tempfile.mkdtemp(prefix=f"skill-foundry-{skill_id}-backup-")
    attempted = False
    keep_backup = False
    try:
        shutil.copytree(current["_dir"], stage, dirs_exist_ok=True)
        shutil.copytree(current["_dir"], backup, dirs_exist_ok=True)
        _write_package(stage, _updated_manifest(current, candidate), candidate["system_md"])
        attempted = True
        result = skills_manager.install_skill_from_dir(stage, force=True)
        if result.get("error"):
            raise RuntimeError(result["error"])
        return skill_id
    except Exception as error:
        if attempted:
            restored = skills_manager.install_skill_from_dir(backup, force=True)
            if restored.get("error"):
                keep_backup = True
                raise RuntimeError(
                    f"{error}; rollback failed: {restored['error']}; backup retained at {backup}"
                ) from error
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        if not keep_backup:
            shutil.rmtree(backup, ignore_errors=True)


def materialize(candidate):
    if candidate.get("action") == "update":
        return _update(candidate)
    return _create(candidate)


def enable(skill_id, enabled=True):
    from backend.skills_manager import skills_manager

    result = skills_manager.set_skill_enabled(skill_id, enabled)
    if result.get("error"):
        raise RuntimeError(result["error"])


def assign(skill_id, agent_id, assigned=True):
    from models.db import db

    if not db.get_agent(agent_id):
        raise ValueError(f"agent not found: {agent_id}")
    skills = db.get_agent_skills(agent_id)
    if assigned and skill_id not in skills:
        skills.append(skill_id)
    if not assigned:
        skills = [item for item in skills if item != skill_id]
    db.set_agent_skills(agent_id, skills)
