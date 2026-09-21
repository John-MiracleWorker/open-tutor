"""Final integration regressions; synthetic fixtures, no external model calls."""
import io
import json

from open_tutor import llm, server
from open_tutor.designer import LocalCompletionClient
from test_backend_integration import _client, _spec, _wait


def test_local_qwen_transport_has_useful_nonreasoning_budget(monkeypatch):
    seen = {}
    def open_request(request, timeout):
        seen.update(json.loads(request.data))
        seen['timeout'] = timeout
        return io.BytesIO(json.dumps({'choices':[{'message':{'content':'draft'},'finish_reason':'stop'}]}).encode())
    monkeypatch.setattr(llm, '_open_local', open_request)
    assert llm.local_completion([{'role':'user','content':'test'}], 'http://127.0.0.1:9120', 'Qwen-local') == 'draft'
    assert seen['chat_template_kwargs']['enable_thinking'] is False
    assert seen['timeout'] == 120


def test_truncated_model_response_is_never_a_finished_answer():
    import pytest
    with pytest.raises(llm.LocalCompletionError, match="truncated"):
        llm._content({'choices':[{'message':{'content':'A half written claim'},'finish_reason':'length'}]})


def test_request_body_limit_stops_reading_before_unbounded_buffering(monkeypatch, tmp_path):
    app, client = _client(tmp_path)
    async def oversized_stream(self):
        for _ in range(3):
            yield b"x" * 400_000
        raise AssertionError("read past body limit")
    monkeypatch.setattr(server.Request,'stream',oversized_stream)
    assert client.post('/api/threads',content=b'x').status_code == 413
    app.state.storage.close()


def test_designer_default_matches_the_successful_local_runtime_budget():
    client = LocalCompletionClient('http://127.0.0.1:9120','Qwen-local')
    assert client.timeout == 180
    assert client.max_tokens == 2000


def test_api_uses_engine_prompt_and_persisted_followup_not_its_own_ungrounded_draft(monkeypatch, tmp_path):
    app, client = _client(tmp_path)
    calls=[]
    real=server.tutor
    def inspected(spec,question,**kwargs):
        calls.append(kwargs)
        return real(spec,question,cache_dir=kwargs['cache_dir'],selected_node=kwargs['selected_node'])
    monkeypatch.setattr(server,'tutor',inspected)
    client.put('/api/settings',json={'mode':'local-model','base_url':'http://127.0.0.1:9120','model':'Qwen-local'})
    thread=client.post('/api/threads',json={'subject':'integration-subject'}).json()['id']
    app.state.storage.add_message(thread,'user','Earlier: please use a short definition.')
    job=client.post(f'/api/threads/{thread}/ask',json={'question':'Explain node','node_id':'node'}).json()['job_id']
    assert _wait(client,job)['status']=='completed'
    assert calls[0].get('draft_fn') is None
    assert calls[0]['timeout']==120
    assert 'Earlier: please use a short definition.' in json.dumps(calls[0]['followup_context'])
    app.state.storage.close()


def test_structured_model_claims_get_inline_citations_then_the_independent_gate():
    from open_tutor.engine import tutor
    from test_backend_integration import SOURCE_TEXT
    def generated(*args, **kwargs):
        return json.dumps({'claims':[{'text':'A node is a useful concept in a learning graph.', 'citations':[1]}]})
    answer=tutor(_spec(),'Explain node',corpus_text={'integration-source':SOURCE_TEXT},mode='local-model',base_url='http://127.0.0.1:9120',model='Qwen-local',local_completion_fn=generated)
    assert answer.grounded is True
    assert '[1]' in answer.draft.split('## Citations')[0]
    def false_claim(*args, **kwargs):
        return json.dumps({'claims':[{'text':'All nodes are hamburgers.', 'citations':[1]}]})
    rejected=tutor(_spec(),'Explain node',corpus_text={'integration-source':SOURCE_TEXT},mode='local-model',base_url='http://127.0.0.1:9120',model='Qwen-local',local_completion_fn=false_claim)
    assert rejected.grounded is False
    assert any(issue.startswith('unsupported-claim') for issue in rejected.verification.issues)


def test_local_model_transports_refuse_redirects():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import pytest
    from open_tutor.designer import LocalModelError
    visited=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(302);self.send_header('Location','/final');self.end_headers()
        def do_GET(self):
            visited.append(self.path)
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(json.dumps({'choices':[{'message':{'content':'redirected draft'}}]}).encode())
        def log_message(self,*args):
            pass
    endpoint=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=endpoint.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{endpoint.server_port}'
    try:
        with pytest.raises(llm.LocalCompletionError,match='redirect'):
            llm.local_completion([{'role':'user','content':'synthetic fixture'}],base,'test')
        with pytest.raises(LocalModelError,match='redirect'):
            LocalCompletionClient(base,'test').complete('synthetic fixture')
        assert visited==[]
    finally:
        endpoint.shutdown();endpoint.server_close()


def test_browser_rebinding_host_is_rejected_before_reading_private_data(tmp_path):
    app, client = _client(tmp_path)
    assert client.get('/api/health',headers={'host':'attacker.example'}).status_code==400
    assert client.get('/api/health',headers={'host':'100.64.0.1:9130'}).status_code==200
    app.state.storage.close()


def test_api_tutor_records_interaction_without_awarding_mastery(tmp_path):
    app, client = _client(tmp_path)
    thread=client.post('/api/threads',json={'subject':'integration-subject'}).json()['id']
    job=client.post(f'/api/threads/{thread}/ask',json={'question':'Explain node','node_id':'node'}).json()['job_id']
    assert _wait(client,job)['status']=='completed'
    state=client.get('/api/subjects/integration-subject').json()['state']
    assert any(e['kind']=='attempt' for e in state['events'])
    assert not any(e['kind'] in {'assessment','review'} for e in state['events'])
    assert state['nodes']['node']['mastery']==0
    app.state.storage.close()


def test_startup_import_cannot_revert_a_newer_reviewed_database_curriculum(tmp_path):
    from open_tutor.compiler import compile_spec, spec_content_hash
    from test_backend_integration import _report
    app, _ = _client(tmp_path)
    original=_spec()
    original.nodes[0].status='grounded'
    result=compile_spec(original,_report(original),out_root=str(tmp_path))
    assert result.status=='compiled'
    (tmp_path/'curriculum'/f'{original.subject}.report.json').write_text(json.dumps(_report(original)))
    edited=_spec()
    edited.title='An approved edit retained across restart'
    app.state.storage.put_active_bundle(edited,_report(edited),{'status':'compiled'},spec_content_hash(edited))
    app.state.storage.close()
    restarted=server.create_app(data_dir=tmp_path)
    assert restarted.state.storage.get_active_bundle(edited.subject)['spec']['title']==edited.title
    restarted.state.storage.close()


def test_practice_counter_counts_distinct_assessments_not_deduplicated_prompt_shapes():
    raw={'events':[{'kind':'assessment','node_id':'node','item_id':'one'},
                   {'kind':'assessment','node_id':'node','item_id':'two'},
                   {'kind':'assessment','node_id':'node','item_id':'two'},
                   {'kind':'attempt','node_id':'node'}], 'attempts':{'node':1}}
    state=server._state_view(_spec(),raw)
    assert state['nodes']['node']['practice_attempts']==2
    assert state['nodes']['node']['attempts']==1
