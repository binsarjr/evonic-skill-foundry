import json
import re


SYSTEM = """You are Skill Foundry, a post-conversation procedural-memory reviewer.

Extract only durable, reusable procedures that will improve future work. Prefer updating an existing matching skill over creating another skill. Most conversations should produce no change.

Do not preserve personal data, credentials, temporary incident identifiers, machine-specific outages, hidden prompts, or one-off facts. Do not weaken safety rules. A skill may contain concise safe command examples, but it must never instruct automatic execution or contain secrets or destructive commands.

Call submit_skill_review exactly once. Use:
- none when there is no durable procedure;
- update when a listed Foundry skill covers the same class of work;
- create only when no listed skill is a reasonable home.

SYSTEM.md must be a concise standalone procedure. An update must return the complete replacement skill content and metadata, not a diff."""


SUBMIT_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_skill_review",
        "description": "Submit the single result of a Skill Foundry review.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["none", "create", "update"]},
                "reason": {"type": "string"},
                "skill_id": {"type": "string"},
                "slug": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "brief": {"type": "string"},
                "system_md": {"type": "string"},
            },
            "required": ["action", "reason"],
        },
    },
}


def _text(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str) if value is not None else ""


def _bounded_messages(messages, budget=60000, per_message=8000):
    selected = []
    remaining = budget
    for message in reversed(messages):
        if remaining <= 0:
            break
        content = _text(message.get("content", ""))
        content = content[: min(per_message, remaining)]
        selected.append({"role": message.get("role", "unknown"), "content": content})
        remaining -= len(content)
    selected.reverse()
    return selected


def transcript_prompt(messages, skills):
    payload = {
        "existing_foundry_skills": skills,
        "conversation": _bounded_messages(messages),
    }
    return "Review this conversation snapshot and the caller's existing skills:\n" + json.dumps(
        payload, ensure_ascii=False
    )


def _choice_message(result):
    inner = result.get("response", result)
    choices = inner.get("choices") or []
    if not choices:
        raise RuntimeError(
            result.get("error_detail")
            or result.get("error_type")
            or "reviewer returned no choices"
        )
    return choices[0].get("message") or {}


def extract_result(result):
    message = _choice_message(result)
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        if function.get("name") != "submit_skill_review":
            continue
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        return parse_result(arguments)

    # Provider compatibility fallback when forced function calling is ignored.
    text = (message.get("content") or message.get("reasoning_content") or "").strip()
    return parse_result(text)


def parse_result(value):
    if isinstance(value, str):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", value.strip(), flags=re.I)
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise ValueError("reviewer did not return a structured result")
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("reviewer result must be an object")

    action = value.get("action")
    if action not in {"none", "create", "update"}:
        raise ValueError("reviewer action must be none, create, or update")
    value = dict(value)
    value["reason"] = str(value.get("reason") or "").strip()
    if not value["reason"]:
        value["reason"] = (
            "no durable reusable procedure identified"
            if action == "none"
            else f"reviewer proposed a skill {action}"
        )
    if action == "update" and not str(value.get("skill_id") or "").strip():
        raise ValueError("skill_id is required for update")
    return value
