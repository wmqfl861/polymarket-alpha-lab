"""Actual pinned Codex binary + loopback synthetic Responses server. No account.

Explicit CI opt-in only. Production transport is untouched; the test replaces
only its provider URL/auth requirement to keep ALL model traffic local/synthetic.
"""
from contextlib import contextmanager
from hashlib import file_digest
import http.server
import json
import os
from pathlib import Path
import threading

import pytest

from polymarket_alpha_lab import research_codex_local as local
from polymarket_alpha_lab.project_postgres.files import private_directory

ENABLED = os.environ.get('POLYMARKET_ALPHA_LAB_TEST_CODEX_BINARY')
CANARY = 'SYNTHETIC-UNAPPROVED-CONTEXT-CANARY'


def action_for(body):
    envelope = json.loads(next(x['content'][0]['text'] for x in reversed(body['input']) if x['role']=='user'))
    messages = json.loads(envelope['messages_json'])
    calls = [c['function']['name'] for m in messages if m['role']=='assistant' for c in m.get('tool_calls', [])]
    if 'search_evidence' not in calls:
        return [{'name':'search_evidence','arguments_json':'{"query":"*"}'}]
    if 'read_evidence' not in calls:
        observation = json.loads(next(m['content'] for m in reversed(messages) if m['role']=='tool'))
        return [{'name':'read_evidence','arguments_json':json.dumps({'source_id':s['source_id']})}
                for s in observation['sources']]
    ids = [json.loads(m['content'])['source_id'] for m in messages if m['role']=='tool'
           and 'source_id' in json.loads(m['content'])]
    return [{'name':'finish_research','arguments_json':json.dumps(dict(probability_yes='0.6', confidence='0.4',
        summary='Synthetic mock response; no real market research.', source_ids=ids))}]


@contextmanager
def synthetic_server(monkeypatch, mode='success'):
    seen=[]
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            seen.append(('GET',self.path,{}));self.send_response(404);self.end_headers()
        def do_POST(self):
            raw=self.rfile.read(int(self.headers['Content-Length']))
            body=json.loads(raw);seen.append(('POST',self.path,body))
            assert 'Authorization' not in self.headers  # No login/key in this test.
            if mode in ('429','500'):
                self.send_response(int(mode));self.send_header('Content-Type','application/json');self.end_headers()
                self.wfile.write(b'{"error":{"message":"synthetic failure"}}');return
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            if mode=='broken-stream':
                self.wfile.write(b'event: response.created\ndata: {"type":"response.created","response":{"id":"r"}}\n\n');return
            text = '{invalid-json' if mode=='invalid-json' else json.dumps({'calls':action_for(body)})
            item={'id':'msg_1','type':'message','status':'completed','role':'assistant',
                  'content':[{'type':'output_text','text':text,'annotations':[]}]}
            rows=[{'type':'response.created','response':{'id':'resp_synthetic','status':'in_progress'}},
                {'type':'response.output_item.added','output_index':0,'item':dict(item,status='in_progress',content=[])},
                {'type':'response.output_text.delta','item_id':'msg_1','output_index':0,'content_index':0,'delta':text},
                {'type':'response.output_item.done','output_index':0,'item':item},
                {'type':'response.completed','response':{'id':'resp_synthetic','status':'completed','output':[item],
                    'usage':{'input_tokens':100,'input_tokens_details':{'cached_tokens':20},
                             'output_tokens':20,'output_tokens_details':{'reasoning_tokens':5},'total_tokens':120}}}]
            try:
                for row in rows:
                    self.wfile.write(('event: '+row['type']+'\ndata: '+json.dumps(row)+'\n\n').encode())
            except (BrokenPipeError,ConnectionResetError):
                pass  # The test client may enforce its deadline; never retry.
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever);thread.start()
    original=local._overrides
    def settings(work):
        out=original(work)
        out.update({'model_providers.pal_codex.base_url':f'http://127.0.0.1:{server.server_port}/v1',
                    'model_providers.pal_codex.requires_openai_auth':False})
        return out
    monkeypatch.setattr(local,'_overrides',settings)
    try:yield seen
    finally:
        server.shutdown();server.server_close();thread.join()
        monkeypatch.setattr(local,'_overrides',original)


def native_profile(tmp_path, model_id='synthetic-model'):
    binary=Path(os.environ['POLYMARKET_ALPHA_LAB_TEST_CODEX_BINARY'])
    with binary.open('rb') as f:checksum=file_digest(f,'sha256').hexdigest()
    auth, scratch=tmp_path/'auth',tmp_path/'scratch'
    private_directory(auth,create=True);private_directory(scratch,create=True)
    return local.CodexLocalProfile(binary,checksum,auth,scratch,model_id,30)


@pytest.mark.skipif(not ENABLED,reason='explicit pinned Codex binary + synthetic loopback proof')
@pytest.mark.parametrize('mode',['success','429','500','broken-stream','invalid-json'])
def test_actual_binary_protocol_retry_and_context_isolation(tmp_path,monkeypatch,mode):
    p=native_profile(tmp_path)
    # Poison user config, parent instructions, host skills and old history. None
    # belongs in the submitted task; the adapter must not load/send any of it.
    (p.codex_home/'config.toml').write_text('deliberately invalid '+CANARY)
    (tmp_path/'AGENTS.md').write_text(CANARY)
    (tmp_path/'.codex').mkdir()
    (tmp_path/'.codex'/'config.toml').write_text('malformed parent configuration '+CANARY)
    (p.codex_home/'history.jsonl').write_text(CANARY)
    skills=p.codex_home/'skills'/'probe';skills.mkdir(parents=True)
    (skills/'SKILL.md').write_text('---\nname: probe\ndescription: '+CANARY+'\n---\n'+CANARY)
    monkeypatch.setenv('OPENAI_API_KEY',CANARY)
    with synthetic_server(monkeypatch,mode) as seen:
        kw=dict(messages_json='[{"role":"user","content":"SYNTHETIC ONLY"}]',max_output_tokens=100,call_number=1)
        if mode=='success':
            result=local.invoke_codex(p,**kw)
            assert result.usage.total_tokens==120 and result.usage.cached_input_tokens==20
        else:
            with pytest.raises(ValueError,match='^research_codex_invocation_failed$'):
                local.invoke_codex(p,**kw)
    assert len(seen)==1 and seen[0][:2]==('POST','/v1/responses')
    body=seen[0][2]
    assert body['tools']==[] and CANARY not in json.dumps(body)
    assert body['instructions']==local.INSTRUCTIONS and body['model']==p.model_id
    assert list(p.workspace_parent.iterdir())==[]
    assert not any(x.name.endswith(('.sqlite','.db','.jsonl')) and x.name!='history.jsonl'
                   for x in p.codex_home.rglob('*'))
    print('pinned Codex synthetic:',mode,'requests=1 tools=0 usage_is_not_invoice=true')


@pytest.mark.skipif(not ENABLED,reason='explicit pinned Codex binary + synthetic loopback proof')
@pytest.mark.parametrize('name',['AGENTS.md','AGENTS.override.md'])
def test_global_auth_instructions_are_refused_not_deleted(tmp_path,monkeypatch,name):
    p=native_profile(tmp_path)
    forbidden=p.codex_home/name;forbidden.write_text(CANARY)
    with synthetic_server(monkeypatch) as seen:
        with pytest.raises(ValueError,match='^research_codex_invocation_failed$'):
            local.invoke_codex(p,messages_json='[{"role":"user","content":"test"}]',max_output_tokens=100,call_number=1)
    assert not seen and forbidden.read_text()==CANARY
    assert list(p.workspace_parent.iterdir())==[]
