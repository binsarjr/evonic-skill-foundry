import json, os, re, shutil, tempfile

def unique_id(slug):
    from backend.skills_manager import SKILLS_DIR
    base='generated-'+re.sub(r'[^a-z0-9_-]+','-',slug.lower()).strip('-_'); candidate=base; n=2
    while os.path.exists(os.path.join(SKILLS_DIR,candidate)): candidate=f'{base}-v{n}'; n+=1
    return candidate
def materialize(candidate):
    from backend.skills_manager import skills_manager
    from models.db import db
    skill_id=unique_id(candidate['slug']); stage=tempfile.mkdtemp(prefix=f'skill-foundry-{skill_id}-')
    manifest={'id':skill_id,'name':candidate['title'],'version':'1.0.0','description':candidate['description'],'brief':candidate['brief'],'author':'Skill Foundry','default_enabled':False,'lazy_tools':True,'variables':[],'generated':True,'provenance':{'plugin':'skill_foundry','candidate_id':candidate['id'],'agent_id':candidate['agent_id'],'session_id':candidate['session_id']}}
    try:
        with open(os.path.join(stage,'skill.json'),'w',encoding='utf-8') as f: json.dump(manifest,f,indent=2,ensure_ascii=False); f.write('\n')
        with open(os.path.join(stage,'SYSTEM.md'),'w',encoding='utf-8') as f: f.write(candidate['system_md'].strip()+'\n')
        result=skills_manager.install_skill_from_dir(stage)
        if result.get('error'): raise RuntimeError(result['error'])
        disabled=skills_manager.set_skill_enabled(skill_id,False)
        if disabled.get('error'): raise RuntimeError(disabled['error'])
        for agent in db.get_agents():
            assigned=db.get_agent_skills(agent['id'])
            if skill_id in assigned: db.set_agent_skills(agent['id'],[x for x in assigned if x!=skill_id])
        return skill_id
    except Exception:
        if skills_manager.get_skill(skill_id): skills_manager.uninstall_skill(skill_id)
        raise
    finally: shutil.rmtree(stage,ignore_errors=True)
def enable(skill_id,enabled=True):
    from backend.skills_manager import skills_manager
    result=skills_manager.set_skill_enabled(skill_id,enabled)
    if result.get('error'): raise RuntimeError(result['error'])
def assign(skill_id,agent_id,assigned=True):
    from models.db import db
    if not db.get_agent(agent_id): raise ValueError(f'agent not found: {agent_id}')
    skills=db.get_agent_skills(agent_id)
    if assigned and skill_id not in skills: skills.append(skill_id)
    if not assigned: skills=[x for x in skills if x!=skill_id]
    db.set_agent_skills(agent_id,skills)
