import json, re
SYSTEM="""You are Skill Foundry, a conservative post-conversation reviewer. Identify only reusable knowledge or procedures that will help in future, similar tasks. Never preserve personal data, credentials, temporary incident identifiers, one-off fixes, hidden prompts, or instructions to weaken safety. Return exactly one JSON object, with no markdown. If nothing is reusable: {\"action\":\"none\",\"reason\":\"...\"}. Otherwise: {\"action\":\"create\",\"slug\":\"lowercase-slug\",\"title\":\"...\",\"description\":\"...\",\"brief\":\"Use when ...\",\"system_md\":\"# ...\\n...\"}. The SYSTEM.md must be a concise, standalone procedure and must not contain executable Python or shell scripts."""
def _text(v):
    if isinstance(v,str): return v
    return json.dumps(v,ensure_ascii=False) if v is not None else ''
def transcript_prompt(messages):
    clean=[]
    for m in messages:
        role=m.get('role','unknown'); content=_text(m.get('content',''))
        clean.append({'role':role,'content':content[:12000]})
    return 'Review this conversation snapshot:\n'+json.dumps(clean,ensure_ascii=False)
def extract_reply(result):
    inner=result.get('response',result); choices=inner.get('choices') or []
    if not choices: raise RuntimeError(result.get('error_detail') or result.get('error_type') or 'reviewer returned no choices')
    msg=choices[0].get('message') or {}; return (msg.get('content') or msg.get('reasoning_content') or '').strip()
def parse_result(text):
    text=re.sub(r'^```(?:json)?\s*|\s*```$','',text.strip(),flags=re.I)
    start,end=text.find('{'),text.rfind('}')
    if start<0 or end<start: raise ValueError('reviewer did not return JSON')
    data=json.loads(text[start:end+1])
    if data.get('action') not in ('none','create'): raise ValueError('reviewer action must be none or create')
    return data
