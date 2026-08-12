import re


SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
SECRET_PATTERNS = [
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    (
        "credential_assignment",
        re.compile(
            r"(?i)(?:api[_ -]?key|access[_ -]?token|password|secret)\s*[:=]\s*[\"']?[^\s\"']{12,}"
        ),
    ),
    ("github_token", re.compile(r"\bgh[opsu]_[A-Za-z0-9]{20,}\b")),
    ("openai_token", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("aws_key", re.compile(r"\bAKIA[A-Z0-9]{16}\b")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*")),
]
RISK_PATTERNS = [
    (
        "prompt_injection",
        re.compile(
            r"(?i)(?:ignore|override|disregard).{0,24}(?:previous|prior|system|developer|safety) instructions"
        ),
    ),
    (
        "prompt_exfiltration",
        re.compile(r"(?i)(?:reveal|print|expose).{0,24}(?:system|developer|hidden) prompt"),
    ),
    (
        "destructive_command",
        re.compile(r"(?i)\b(?:rm\s+-rf|mkfs\.|dd\s+if=|curl\b[^\n|]*\|\s*(?:sh|bash)|sudo\s+)"),
    ),
    (
        "privilege_escalation",
        re.compile(r"(?i)\b(?:privilege escalation|setuid|chmod\s+u\+s)\b"),
    ),
]


def validate_candidate(data):
    errors = []
    for key in ("slug", "title", "description", "brief", "system_md"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            errors.append(f"{key} is required")

    slug = data.get("slug", "")
    if slug and not SLUG_RE.fullmatch(slug):
        errors.append("slug must match ^[a-z][a-z0-9_-]{2,63}$")
    for key, limit in (("title", 160), ("description", 1000), ("brief", 500)):
        if len(data.get(key, "")) > limit:
            errors.append(f"{key} exceeds {limit} characters")
    if len(data.get("system_md", "").encode()) > 65536:
        errors.append("system_md exceeds 64 KiB")

    text = "\n".join(
        str(data.get(key, "")) for key in ("title", "description", "brief", "system_md")
    )
    risks = [
        {
            "code": name,
            "severity": "critical" if (name, pattern) in SECRET_PATTERNS else "high",
        }
        for name, pattern in SECRET_PATTERNS + RISK_PATTERNS
        if pattern.search(text)
    ]
    if risks:
        errors.append("candidate contains a blocked secret or high-risk instruction")
    return errors, risks
