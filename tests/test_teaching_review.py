import json
import sqlite3
import pytest
from open_tutor.storage import Database
from open_tutor.teaching import TeachingState, generate_teaching, select_plan
from open_tutor.llm import LocalCompletionError
from test_teaching_server import _setup, _wait

@pytest.mark.parametrize('state',[{'approach':'made-up'},{'pace':'fastest'},{'hint_level':-1},{'turn_count':True},{'pending_question':24}])
def test_corrupt_typed_state_is_not_silently_reset(state):
    with pytest.raises(ValueError,match='corrupt'):
        TeachingState.from_dict(state)

@pytest.mark.parametrize('table,column', [('learner_preferences','preferences_json'),('thread_teaching_state','state_json')])
def test_corrupt_persisted_json_is_visible(tmp_path,table,column):
    db=Database(data_dir=tmp_path); thread=db.create_thread('demo')['id']
    db.put_preferences({'goal':'learn'});db.put_teaching_state(thread,{'node_id':'n'})
    with sqlite3.connect(db.path) as c:c.execute(f"UPDATE {table} SET {column}='not-json'")
    with pytest.raises(ValueError,match='corrupt'):
        db.get_preferences() if table=='learner_preferences' else db.get_thread(thread)


def test_corrupt_preferences_api_reports_a_named_failure(tmp_path):
    app,client=_setup(tmp_path)
    app.state.storage.put_preferences({'goal':'learn'})
    with sqlite3.connect(app.state.storage.path) as c:
        c.execute("UPDATE learner_preferences SET preferences_json='not-json'")
    response=client.get('/api/learner/preferences')
    assert response.status_code==500
    assert 'corrupt' in response.json()['detail']


def test_model_repair_receives_the_actual_invalid_object(monkeypatch):
    calls=[];bad='{"title":"T","concept_map":null}'
    def complete(messages,*a,**kw):
        calls.append(messages)
        if len(calls)==1:return bad
        assert any(m['role']=='assistant' and m['content']==bad for m in messages)
        return json.dumps({'title':'T','explanation':'One idea.','steps':[],'diagram':None,'activity':{'kind':'reflect','prompt':'Explain it.'},'evidence_refs':[1]})
    monkeypatch.setattr('open_tutor.teaching.local_completion',complete)
    generate_teaching(plan=select_plan('Teach'),history=[],pending_question=None,request='Teach',evidence=[{'ref':1,'text':'Source'}],oracle={},profile={},base_url='http://localhost:9120',model='test')


def test_no_node_blocked_turn_keeps_resumable_task_context(tmp_path):
    # Re-adjudicated in review round 2: when the evidence result resolves NO
    # node, the failure proves no concept change (a resolvable new-concept ask
    # still resets via the changed-node rule), so it has no authority to wipe
    # the learner's pending task.  The blocked card stays visible; the durable
    # task survives for hint/resume.
    app,client=_setup(tmp_path);thread=client.post('/api/threads',json={'subject':'demo'}).json()['id']
    app.state.storage.put_teaching_state(thread,{'node_id':'node','pending_question':'Old question','turn_count':2,'hint_level':2})
    result=_wait(client,client.post(f'/api/threads/{thread}/ask',json={'question':'zzzx '*130,'teaching':True}).json()['job_id'])
    assert result['result']['status']=='coaching-blocked'
    state=client.get(f'/api/threads/{thread}').json()['teaching_state']
    assert state['node_id']=='node'
    assert state['pending_question']=='Old question'
    assert state['hint_level']==2 and state['turn_count']==2


def test_same_node_outage_preserves_last_activity_for_retry(tmp_path,monkeypatch):
    app,client=_setup(tmp_path);thread=client.post('/api/threads',json={'subject':'demo'}).json()['id']
    app.state.storage.put_teaching_state(thread,{'node_id':'node','pending_question':'Old question','turn_count':2,'hint_level':1})
    def offline(**kwargs):raise LocalCompletionError('offline')
    monkeypatch.setattr('open_tutor.server.generate_teaching',offline)
    result=_wait(client,client.post(f'/api/threads/{thread}/ask',json={'question':'more please','node_id':'node','teaching':True}).json()['job_id'])
    assert result['result']['status']=='coaching-fallback'
    state=client.get(f'/api/threads/{thread}').json()['teaching_state']
    assert state['pending_question']=='Old question'
    assert state['turn_count']==2 and state['hint_level']==1


def test_hint_on_pending_task_survives_model_outage_host_composed(tmp_path,monkeypatch):
    app,client=_setup(tmp_path);thread=client.post('/api/threads',json={'subject':'demo'}).json()['id']
    app.state.storage.put_teaching_state(thread,{'node_id':'node','pending_question':'Old question','turn_count':2,'hint_level':1})
    def offline(**kwargs):raise LocalCompletionError('offline')
    monkeypatch.setattr('open_tutor.server.generate_teaching',offline)
    result=_wait(client,client.post(f'/api/threads/{thread}/ask',json={'question':'hint','node_id':'node','teaching':True,'action':'hint'}).json()['job_id'])
    teaching=result['result']['teaching']
    assert teaching['status']=='ready' and teaching['model'] is None
    assert teaching['activity']['prompt']=='Old question'
    assert '?' not in teaching['explanation']
    state=client.get(f'/api/threads/{thread}').json()['teaching_state']
    assert state['pending_question']=='Old question'
    assert state['turn_count']==3 and state['hint_level']==2 and state['last_action']=='hint'


def test_got_it_transfer_is_host_composed_not_model_near_repeat(tmp_path,monkeypatch):
    app,client=_setup(tmp_path);thread=client.post('/api/threads',json={'subject':'demo'}).json()['id']
    app.state.storage.put_teaching_state(thread,{'node_id':'node','pending_question':'Old question','turn_count':2,'hint_level':1})
    def offline(**kwargs):raise LocalCompletionError('offline')
    monkeypatch.setattr('open_tutor.server.generate_teaching',offline)
    result=_wait(client,client.post(f'/api/threads/{thread}/ask',json={'question':'got it','node_id':'node','teaching':True,'action':'got-it'}).json()['job_id'])
    teaching=result['result']['teaching']
    assert teaching['status']=='ready' and teaching['model'] is None
    assert teaching['intent']=='transfer' and teaching['approach']=='challenge'
    # A NEW situation, not the prior question near-repeated.
    assert teaching['activity']['prompt']!='Old question'
    assert teaching['activity']['kind']=='apply'
    assert '?' not in teaching['explanation']
    assert teaching['evidence_refs']
    state=client.get(f'/api/threads/{thread}').json()['teaching_state']
    assert state['pending_question']==teaching['activity']['prompt']
    assert state['turn_count']==3 and state['last_action']=='got-it' and state['hint_level']==1
