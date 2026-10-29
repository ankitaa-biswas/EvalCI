// Real headless Chrome verification via DevTools; no browser package download.
// node scripts/verify_dashboard.cjs API_URL PRIVATE_ENV_FILE [CHROME_EXECUTABLE]
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn, execFileSync} = require('node:child_process');
const assert = require('node:assert/strict');
const [apiUrl, envFile, chromePath = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'] = process.argv.slice(2);
const values = Object.fromEntries(fs.readFileSync(envFile,'utf8').trim().split(/\r?\n/).map(line => { const p=line.indexOf('='); return [line.slice(0,p),line.slice(p+1)]; }));
const apiKey = values.EVALCI_API_KEYS.split(',')[0];
const output = path.resolve('artifacts/cycle2');
fs.mkdirSync(output,{recursive:true});
const profile = fs.mkdtempSync(path.join(os.tmpdir(),'evalci-cycle2-chrome-'));
const browser = spawn(chromePath,['--headless=new','--disable-gpu','--no-first-run','--disable-background-networking',`--user-data-dir=${profile}`,'--remote-debugging-port=0','about:blank'],{stdio:'ignore'});
const sleep = ms => new Promise(resolve => setTimeout(resolve,ms));
let socket;
(async () => {
  let port;
  for (let attempt=0;attempt<100;attempt++) {
    const file = path.join(profile,'DevToolsActivePort');
    if (fs.existsSync(file)) { port=fs.readFileSync(file,'utf8').split('\n')[0]; break; }
    await sleep(100);
  }
  if (!port) throw new Error('Chrome did not expose a DevTools endpoint');
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  socket = new WebSocket(targets.find(t => t.type==='page').webSocketDebuggerUrl);
  await new Promise((resolve,reject) => {socket.onopen=resolve;socket.onerror=reject;});
  let sequence=0;
  const pending = new Map();
  const exceptions = [];
  socket.onmessage = message => {
    const data=JSON.parse(message.data);
    if (data.id && pending.has(data.id)) {
      const {resolve,reject,timer}=pending.get(data.id);pending.delete(data.id);clearTimeout(timer);
      data.error ? reject(new Error(data.error.message)) : resolve(data.result);
    } else if (data.method==='Runtime.exceptionThrown') exceptions.push(data.params.exceptionDetails.text);
  };
  const command=(method,params={})=>new Promise((resolve,reject)=>{
    const id=++sequence;
    const timer=setTimeout(()=>{pending.delete(id);reject(new Error(`DevTools timeout: ${method}`));},15000);
    pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method,params}));
  });
  const evaluate = async expression => {
    const result=await command('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result.value;
  };
  await command('Page.enable');await command('Runtime.enable');
  await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await command('Page.navigate',{url:`${apiUrl}/dashboard/`});
  for(let i=0;i<100;i++) {if(await evaluate("Boolean(document.getElementById('api-key')) && document.readyState === 'complete'"))break;await sleep(100);}
  await evaluate(`document.getElementById('api-key').value=${JSON.stringify(apiKey)}; document.getElementById('api-key-form').requestSubmit();`);
  const ready=await (await fetch(`${apiUrl}/ready`)).json();
  assert.equal(ready.evaluation_mode,'fixture','Browser verification requires fixture mode');
  const response=await fetch(`${apiUrl}/evaluate/`,{method:'POST',headers:{'Content-Type':'application/json','X-API-Key':apiKey},body:JSON.stringify({commit_sha:execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(),branch:'cycle2-browser-verification',rag_endpoint:'http://rag-target:8001/query',baseline_bootstrap:true})});
  assert.equal(response.status,202);
  const {run_id:runId}=await response.json();
  await evaluate('loadRuns()');
  await evaluate(`document.querySelector('[data-run-id="${runId}"]').click()`);
  const observations=[];
  let captured=false, terminal=false;
  for(let i=0;i<600;i++) {
    const state=await evaluate(`({label:document.getElementById('progress-label').textContent,value:document.getElementById('evaluation-progress').value,max:document.getElementById('evaluation-progress').max,mode:document.getElementById('evaluation-mode').textContent,error:document.getElementById('progress-error').textContent})`);
    if (!observations.length || observations.at(-1).label!==state.label) observations.push(state);
    if (state.label.includes('running') && state.value>0 && state.value<state.max && !captured) {
      const screenshot=await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});
      fs.writeFileSync(path.join(output,'dashboard-progress.png'),Buffer.from(screenshot.data,'base64'));
      captured=true;
    }
    if (state.label.startsWith('complete:') && state.value===50) {terminal=true;break;}
    if(state.label.includes('failed'))throw new Error(`Run ${runId}: ${state.error||state.label}`);
    await sleep(100);
  }
  assert.ok(captured,'Dashboard must display progress before terminal state');
  assert.ok(observations.some(s=>s.label.startsWith('rag requests:') && s.value>0 && s.value<50));
  assert.ok(observations.some(s=>s.label.startsWith('scoring:') && s.value>0 && s.value<50));
  assert.ok(terminal,'Dashboard must display terminal completion');
  await sleep(500);
  assert.match(await evaluate("document.getElementById('evaluation-mode').textContent"),/fixture/);
  assert.deepEqual(exceptions,[]);
  const screenshot=await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});
  fs.writeFileSync(path.join(output,'dashboard-complete.png'),Buffer.from(screenshot.data,'base64'));
  const evidence={mode:'fixture',run_id:runId,real_browser:'Chrome headless via DevTools',observations,uncaught_exceptions:exceptions};
  fs.writeFileSync(path.join(output,'dashboard-verification.json'),JSON.stringify(evidence,null,2));
  console.log(JSON.stringify({run_id:runId,progress_observed:true,request_and_scoring_stages:true,terminal_displayed:true,uncaught_exceptions:exceptions.length}));
})().catch(error=>{console.error(error.message);process.exitCode=1;}).finally(()=>{socket?.close();browser.kill('SIGTERM');});
