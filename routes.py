import os

from flask import Blueprint, jsonify, render_template, request, session

from .service import (
    approve,
    config_errors,
    reject,
    repository,
    review,
    set_assigned,
    set_enabled,
)


PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))


def authorized():
    return bool(session.get("authenticated"))


def actor():
    return str(session.get("user_id") or session.get("username") or "authenticated-user")


def body():
    return request.get_json(silent=True) or {}


def reason(data, default="dashboard action"):
    return str(data.get("reason") or default).strip()


def guarded(function):
    try:
        return jsonify({"success": True, "candidate": function()})
    except KeyError:
        return jsonify({"error": "candidate not found"}), 404
    except (ValueError, RuntimeError) as error:
        return jsonify({"error": str(error)}), 400
    except Exception as error:
        return jsonify({"error": str(error)}), 500


def create_blueprint():
    blueprint = Blueprint(
        "skill_foundry", __name__, template_folder=os.path.join(PLUGIN_DIR, "templates")
    )

    @blueprint.get("/skill-foundry")
    def dashboard():
        if not authorized():
            return jsonify({"error": "unauthorized"}), 401
        return render_template("skill_foundry.html")

    @blueprint.get("/api/skill-foundry/candidates")
    def candidates():
        if not authorized():
            return jsonify({"error": "unauthorized"}), 401
        limit = max(1, min(200, request.args.get("limit", 100, type=int)))
        return jsonify({"candidates": repository().list_candidates(limit)})

    @blueprint.get("/api/skill-foundry/candidates/<candidate_id>")
    def candidate(candidate_id):
        if not authorized():
            return jsonify({"error": "unauthorized"}), 401
        item = repository().get_candidate(candidate_id)
        return jsonify({"candidate": item}) if item else (jsonify({"error": "not found"}), 404)

    @blueprint.get("/api/skill-foundry/audit")
    def audits():
        if not authorized():
            return jsonify({"error": "unauthorized"}), 401
        return jsonify({"audit": repository().audits()})

    @blueprint.get("/api/skill-foundry/status")
    def status():
        if not authorized():
            return jsonify({"error": "unauthorized"}), 401
        from backend.plugin_manager import plugin_manager
        from backend.skills_manager import skills_manager
        from models.db import db

        config = plugin_manager.get_plugin_config("skill_foundry")
        agents = db.get_agents()
        enabled_agents = sum(
            bool(
                plugin_manager.get_agent_plugin_settings("skill_foundry", agent["id"]).get(
                    "enabled", True
                )
            )
            for agent in agents
        )
        generated = sum(
            (skill.get("provenance") or {}).get("plugin") == "skill_foundry"
            for skill in skills_manager.list_skills()
        )
        return jsonify(
            {
                "config_errors": config_errors(config),
                "automatic": bool(config.get("AUTO_REVIEW_ENABLED", True)),
                "approval_required": bool(config.get("REQUIRE_APPROVAL", False)),
                "threshold": max(1, int(config.get("REVIEW_EVERY_TOOL_CALLS", 10))),
                "agents": {"enabled": enabled_agents, "total": len(agents)},
                "generated_skills": generated,
                "stats": repository().stats(),
            }
        )

    @blueprint.post("/api/skill-foundry/generate")
    def generate():
        if not authorized():
            return jsonify({"error": "unauthorized"}), 401
        data = body()
        agent_id = str(data.get("agent_id") or "")
        session_id = str(data.get("session_id") or "")
        if not agent_id or not session_id:
            return jsonify({"error": "agent_id and session_id are required"}), 400
        from backend.plugin_manager import plugin_manager
        from backend.plugin_sdk import PluginSDK

        config = plugin_manager.get_plugin_config("skill_foundry")
        sdk = PluginSDK("skill_foundry", config, {})
        limit = min(500, max(10, int(config.get("TRANSCRIPT_MESSAGE_LIMIT", 100))))
        messages = sdk.get_session_messages(session_id, agent_id, limit=limit)
        return guarded(
            lambda: review(agent_id, session_id, messages, config, True, actor())
        )

    @blueprint.post("/api/skill-foundry/candidates/<candidate_id>/approve")
    def approve_route(candidate_id):
        if not authorized():
            return jsonify({"error": "unauthorized"}), 401
        from backend.plugin_manager import plugin_manager

        data = body()
        return guarded(
            lambda: approve(
                candidate_id,
                plugin_manager.get_plugin_config("skill_foundry"),
                actor(),
                reason(data, "approved in dashboard"),
            )
        )

    @blueprint.post("/api/skill-foundry/candidates/<candidate_id>/reject")
    def reject_route(candidate_id):
        if not authorized():
            return jsonify({"error": "unauthorized"}), 401
        data = body()
        why = reason(data, "")
        if not why:
            return jsonify({"error": "reason is required"}), 400
        return guarded(lambda: reject(candidate_id, actor(), why))

    @blueprint.post("/api/skill-foundry/candidates/<candidate_id>/enable")
    def enable_route(candidate_id):
        if not authorized():
            return jsonify({"error": "unauthorized"}), 401
        return guarded(
            lambda: set_enabled(candidate_id, actor(), bool(body().get("enabled", True)))
        )

    @blueprint.post("/api/skill-foundry/candidates/<candidate_id>/assign")
    def assign_route(candidate_id):
        if not authorized():
            return jsonify({"error": "unauthorized"}), 401
        data = body()
        return guarded(
            lambda: set_assigned(
                candidate_id,
                actor(),
                bool(data.get("assigned", True)),
                data.get("agent_id"),
            )
        )

    return blueprint
