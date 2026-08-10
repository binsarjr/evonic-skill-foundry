import re
SLUG_RE=re.compile(r'^[a-z][a-z0-9_-]{2,63}$')
SECRET_PATTERNS=[
 ('private_key',re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')),
 ('api_token',re.compile(r'(?i)(?:api[_ -]?key|access[_ -]?token|secret)\s*[:=]\s*["\']?[A-Za-z0-9_\-]{16,}')),
 ('github_token',re.compile(r'\bgh[opsu]_[A-Za-z0-9]{20,}\b')),
]
RISK_PATTERNS=[
 ('prompt_injection',re.compile(r'(?i)ignore (?:all )?(?:previous|prior|system) instructions')),
 ('destructive_command',re.compile(r'(?i)\b(?:rm\s+-rf|mkfs\.|dd\s+if=|curl\b[^\n|]*\|\s*(?:sh|bash)|sudo\s+)')),
 ('privilege_escalation',re.compile(r'(?i)\b(?:privilege escalation|setuid|chmod\s+u\+s)\b')),
]
def validate_candidate(data):
    errors=[]
    for key in ('slug','title','description','brief','system_md'):
        if not isinstance(data.get(key),str) or not data[key].strip(): errors.append(f'{key} is required')
    if data.get('slug') and not SLUG_RE.fullmatch(data['slug']): errors.append('slug must match ^[a-z][a-z0-9_-]{2,63}$')
    if len(data.get('system_md','').encode())>65536: errors.append('system_md exceeds 64 KiB')
    text='\n'.join(str(data.get(k,'')) for k in ('title','description','brief','system_md'))
    risks=[{'code':name,'severity':'critical' if name in {'private_key','api_token','github_token'} else 'high'} for name,p in SECRET_PATTERNS+RISK_PATTERNS if p.search(text)]
    if risks: errors.append('candidate contains blocked secret or high-risk instruction')
    return errors,risks
