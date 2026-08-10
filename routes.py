import os
from flask import Blueprint, jsonify, render_template, request, session
from .service import approve, config_errors, reject, repository, review, set_assigned, set_enabled
PLUGIN_DIR=os.path.dirname(os.path.abspath(__file__))
def authorized():return bool(session.get('authenticated'))
def actor():return str(session.get('user_id') or session.get('username') or 'authenticated-user')
def body():return request.get_json(silent=True) or {}
def reason(data,default='dashboard action'):return str(data.get('reason') or default).strip()
def guarded(fn):
    try:return jsonify({'success':True,'candidate':fn()})
    except KeyError:return jsonify({'error':'candidate not found'}),404
    except (ValueError,RuntimeError) as e:return jsonify({'error':str(e)}),400
    except Exception as e:return jsonify({'error':str(e)}),500
def create_blueprint():
    bp=Blueprint('skill_foundry',__name__,template_folder=os.path.join(PLUGIN_DIR,'templates'))
    @bp.get('/skill-foundry')
    def dashboard():
        if not authorized():return jsonify({'error':'unauthorized'}),401
        return render_template('skill_foundry.html')
    @bp.get('/api/skill-foundry/candidates')
    def candidates():
        if not authorized():return jsonify({'error':'unauthorized'}),401
        return jsonify({'candidates':repository().list_candidates(min(200,request.args.get('limit',100,type=int)))})
    @bp.get('/api/skill-foundry/candidates/<cid>')
    def candidate(cid):
        if not authorized():return jsonify({'error':'unauthorized'}),401
        c=repository().get_candidate(cid)
        return (jsonify({'candidate':c}) if c else (jsonify({'error':'not found'}),404))
    @bp.get('/api/skill-foundry/audit')
    def audits():
        if not authorized():return jsonify({'error':'unauthorized'}),401
        return jsonify({'audit':repository().audits()})
    @bp.get('/api/skill-foundry/status')
    def status():
        if not authorized():return jsonify({'error':'unauthorized'}),401
        from backend.plugin_manager import plugin_manager
        cfg=plugin_manager.get_plugin_config('skill_foundry'); return jsonify({'config_errors':config_errors(cfg)})
    @bp.post('/api/skill-foundry/generate')
    def generate():
        if not authorized():return jsonify({'error':'unauthorized'}),401
        data=body(); agent_id=str(data.get('agent_id') or ''); session_id=str(data.get('session_id') or '')
        if not agent_id or not session_id:return jsonify({'error':'agent_id and session_id are required'}),400
        from backend.plugin_manager import plugin_manager
        from backend.plugin_sdk import PluginSDK
        cfg=plugin_manager.get_plugin_config('skill_foundry'); sdk=PluginSDK('skill_foundry',cfg,{})
        messages=sdk.get_session_messages(session_id,agent_id,limit=min(500,max(10,int(cfg.get('TRANSCRIPT_MESSAGE_LIMIT',100)))))
        return guarded(lambda:review(agent_id,session_id,messages,cfg,True,actor()))
    @bp.post('/api/skill-foundry/candidates/<cid>/approve')
    def approve_route(cid):
        if not authorized():return jsonify({'error':'unauthorized'}),401
        data=body()
        from backend.plugin_manager import plugin_manager
        return guarded(lambda:approve(cid,plugin_manager.get_plugin_config('skill_foundry'),actor(),reason(data,'approved in dashboard')))
    @bp.post('/api/skill-foundry/candidates/<cid>/reject')
    def reject_route(cid):
        if not authorized():return jsonify({'error':'unauthorized'}),401
        data=body(); why=reason(data,'')
        if not why:return jsonify({'error':'reason is required'}),400
        return guarded(lambda:reject(cid,actor(),why))
    @bp.post('/api/skill-foundry/candidates/<cid>/enable')
    def enable_route(cid):
        if not authorized():return jsonify({'error':'unauthorized'}),401
        return guarded(lambda:set_enabled(cid,actor(),bool(body().get('enabled',True))))
    @bp.post('/api/skill-foundry/candidates/<cid>/assign')
    def assign_route(cid):
        if not authorized():return jsonify({'error':'unauthorized'}),401
        data=body(); return guarded(lambda:set_assigned(cid,actor(),bool(data.get('assigned',True)),data.get('agent_id')))
    return bp
