"""Real Codex MCP consent, reload and standalone OAuth, with local synthetic services.

No account, API key, browser window or paid model request is used. All native
configuration and chats are disposable. Run --out outside the checkout.
"""
import json, os, sys, tempfile, threading, subprocess, time, urllib.request, urllib.parse
from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from probe_codex_remembered_approvals import Rpc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class Model(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def send_json(self,value,code=200,extra=None):
  data=json.dumps(value).encode();self.send_response(code)
  for k,v in (extra or {}).items(): self.send_header(k,v)
  self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def do_GET(self):
  origin='http://127.0.0.1:'+str(self.server.server_port)
  if self.path.startswith('/.well-known/oauth-protected-resource'):
   return self.send_json({'resource':origin+'/mcp','authorization_servers':[origin]})
  if self.path.startswith('/.well-known/oauth-authorization-server'):
   return self.send_json({'issuer':origin,'authorization_endpoint':origin+'/authorize','token_endpoint':origin+'/token','registration_endpoint':origin+'/register','response_types_supported':['code'],'grant_types_supported':['authorization_code','refresh_token'],'code_challenge_methods_supported':['S256'],'token_endpoint_auth_methods_supported':['none']})
  if self.path.startswith('/authorize'):
   params=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
   redirect=params['redirect_uri'][0]
   parsed=urllib.parse.urlsplit(redirect)
   if '\r' in redirect or '\n' in redirect or parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.username or parsed.password:
    return self.send_json({},400)
   target=redirect+'?'+urllib.parse.urlencode({'code':'local-test-code','state':params['state'][0]})
   if '\r' in target or '\n' in target: return self.send_json({},400)
   self.send_response(302);self.send_header('Location',urllib.parse.quote(target,safe=':/?=&%'));self.end_headers();return
  return self.send_json({},404)
 def do_POST(self):
  if self.path=='/register':
   body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
   return self.send_json(dict(body,client_id='local-test-client',token_endpoint_auth_method='none'),201)
  if self.path=='/token':
   self.rfile.read(int(self.headers['Content-Length']))
   return self.send_json({'access_token':'local-test-token','token_type':'Bearer','expires_in':3600})
  if self.path=='/mcp':
   self.rfile.read(int(self.headers['Content-Length']))
   return self.send_json({},401,{'WWW-Authenticate':'Bearer resource_metadata="http://127.0.0.1:'+str(self.server.server_port)+'/.well-known/oauth-protected-resource"'})
  body=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
  item={"type":"message","role":"assistant","id":"probe-message","content":[{"type":"output_text","text":"Probe complete."}]}
  if 'MCP_PROBE' in json.dumps(body) and not any(i.get('type')=='function_call_output' for i in body.get('input',[])[max((j for j,i in enumerate(body.get('input',[])) if i.get('role')=='user'),default=0):]):
   names=[(t['name'],c['name']) for t in body.get('tools',[]) for c in t.get('tools',[]) if c.get('name')=='probe_tool']
   assert len(names)==1,names
   item={'type':'function_call','call_id':'mcp-test-call','namespace':names[0][0],'name':names[0][1],'arguments':'{}'}
  events=[{"type":"response.created","response":{"id":"probe"}},{"type":"response.output_item.done","item":item},{"type":"response.completed","response":{"id":"probe","usage":{"input_tokens":0,"output_tokens":0,"total_tokens":0}}}]
  data="".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
  self.send_response(200);self.send_header("Content-Type","text/event-stream");self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)


@contextmanager
def isolated_storage(home):
    """Keep native storage overrides local to this disposable probe."""
    names = ("CODEX_HOME", "CODEX_SQLITE_HOME")
    previous = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ[name] = str(home)
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def run(binary):
    report={'codex':subprocess.check_output([binary,'--version'],text=True).strip(),'platform':sys.platform}
    server=ThreadingHTTPServer(('127.0.0.1',0),Model);threading.Thread(target=server.serve_forever,daemon=True).start()
    root=Path(tempfile.mkdtemp(prefix='tcp-tools-probe-'));home=root/'home';work=root/'work';home.mkdir();work.mkdir()
    mcp=root/'mcp.py'
    mcp.write_text(dedent('''
    import json,sys
    for line in sys.stdin:
     m=json.loads(line)
     if 'id' not in m: continue
     method=m.get('method')
     result={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'probe','version':'1'}} if method=='initialize' else {'tools':[{'name':'probe_tool','description':'Synthetic probe tool','inputSchema':{'type':'object','properties':{}},'annotations':{'destructiveHint':True,'readOnlyHint':False}}]} if method=='tools/list' else {'content':[{'type':'text','text':'Synthetic tool called'}]} if method=='tools/call' else {}
     print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':result}),flush=True)
    '''))
    (home/'config.toml').write_text('model="gpt-5.1-codex"\n[windows]\nsandbox="unelevated"\n[mcp_servers.probe]\ncommand='+json.dumps(sys.executable)+'\nargs=['+json.dumps(str(mcp))+']\n')
    with (home/'config.toml').open('a') as f: f.write('\nmodel_provider="probe"\n')
    # The provider belongs at top level, before any tables.
    cfg=(home/'config.toml').read_text().replace('\nmodel_provider="probe"\n','');(home/'config.toml').write_text('model_provider="probe"\n'+cfg+'\n[model_providers.probe]\nname="probe"\nbase_url="http://127.0.0.1:'+str(server.server_port)+'/v1"\nwire_api="responses"\nrequires_openai_auth=false\n')
    with (home/'config.toml').open('a') as f: f.write('\n[mcp_servers.oauth]\nurl="http://127.0.0.1:'+str(server.server_port)+'/mcp"\n')
    env=dict(os.environ,CODEX_HOME=str(home),CODEX_SQLITE_HOME=str(home));env.pop('OPENAI_API_KEY',None);env.pop('CODEX_API_KEY',None)
    rpc=Rpc.__new__(Rpc)
    try:
     rpc.__init__(binary,home,work,None,env);rpc.handle=lambda m: None
     start={'cwd':str(work),'approvalPolicy':'on-request','sandbox':'read-only','config':{'features.apps':False,'mcp_servers.probe.enabled':False}}
     first=rpc.request('thread/start',start);tid=first['thread']['id'];rpc.request('turn/start',{'threadId':tid,'input':[{'type':'text','text':'Synthetic probe'}]})
     while True:
      m=rpc.read()
      if m.get('method')=='turn/completed': break
     time.sleep(1)
     def status():
      rows=rpc.request('mcpServerStatus/list',{'threadId':tid,'limit':100})['data']
      return [{'name':r['name'],'tools':list(r.get('tools',{})),'status':r.get('runtimeStatus')} for r in rows]
     report['initial']=status();assert not any(row['tools'] for row in report['initial'] if row['name']=='probe')
     changed=dict(start,threadId=tid,config={'features.apps':False,'mcp_servers.probe.enabled':True})
     rpc.request('thread/resume',changed);rpc.request('config/mcpServer/reload',{});time.sleep(2)
     report['loaded_resume_then_reload']=status();assert not any(row['tools'] for row in report['loaded_resume_then_reload'] if row['name']=='probe')
     second=rpc.request('thread/start',dict(start,config={'features.apps':False,'mcp_servers.probe.enabled':True}));time.sleep(2)
     enabled=rpc.request('mcpServerStatus/list',{'threadId':second['thread']['id'],'limit':100})['data']
     assert any(row['name']=='probe' and 'probe_tool' in row.get('tools',{}) for row in enabled)
     from thechatplace.providers.codex_requests import decode_request, ELICITATION
     report['tool_consent']=[]
     for decision in ('decline','accept','decline'):
      observed={'decision':decision,'requests':[],'status':None}
      def handle(message):
       if 'id' in message and 'method' in message:
        assert message['method']==ELICITATION
        request=decode_request(message['id'],message['method'],message['params'])
        assert 'Tool arguments:' in request.elicitation().message
        observed['requests'].append(message['method'])
        rpc.send({'id':message['id'],'result':request.elicitation_answer(decision,{} if decision=='accept' else None)})
       if message.get('method')=='item/completed' and message['params']['item']['type']=='mcpToolCall':
        observed['status']=message['params']['item']['status']
      rpc.handle=handle
      rpc.request('turn/start',{'threadId':second['thread']['id'],'input':[{'type':'text','text':'MCP_PROBE '+decision}]})
      while True:
       message=rpc.read();handle(message)
       if message.get('method')=='turn/completed': break
      assert observed['requests']==[ELICITATION]
      assert observed['status']==('completed' if decision=='accept' else 'failed')
      report['tool_consent'].append(observed)
     rpc.handle=lambda message: None
     # Exercise TCP's production standalone login connection with the same isolated home.
     sys.path.insert(0,str(Path.cwd()))
     from thechatplace.providers.codex_tools import CodexToolLogins
     with isolated_storage(home):
      service=CodexToolLogins(binary)
      try:
       result=service.start(str(work),'oauth')
       urllib.request.urlopen(result['authorizationUrl'],timeout=10).read()
       for _ in range(600):
        completed=service.result(str(work),'oauth',result['loginId'])
        if completed is not None: break
        time.sleep(.05)
       assert completed is True
       assert not service._attempts
       report['standalone_oauth_completed']=completed
      finally: service.close()
     report['temporary_folder']=str(root)
    finally:
     try:
      if hasattr(rpc, "process"): rpc.close()
     finally:
      server.shutdown();server.server_close()
    
    return report


if __name__ == '__main__':
    import argparse
    from thechatplace import platform_paths
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',default=None)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    result=run(args.binary or platform_paths.find_codex())
    Path(args.out).write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))
