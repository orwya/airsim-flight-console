const {test} = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const {readFileSync} = require("node:fs");
const {join} = require("node:path");
const source = readFileSync(join(__dirname, "../static/planner.js"), "utf8");
const settle = () => new Promise(resolve => setImmediate(resolve));

function page() {
  const elements = new Map(), requests = [];
  function element(selector) {
    if (!elements.has(selector)) elements.set(selector, {
      value: "", textContent: "", disabled: false, hidden: false, checked: true,
      dataset: {}, scrollHeight: 100, scrollTop: 0, clientHeight: 100,
      listeners: {}, addEventListener(type, callback) {this.listeners[type] = callback;},
    });
    return elements.get(selector);
  }
  const response = (body, status=200) => ({ok:status>=200 && status<300, status, json:async()=>body});
  const state = {value:"idle", logs:{items:[],next_cursor:0,truncated:false}, calls:requests};
  const context = vm.createContext({
    plannerEnabled:false, plannerState:"disabled", scenePhase:"ready", sceneBusy:false,
    weatherPending:false, history:[], document:{hidden:false,body:{dataset:{plannerTimeout:"0.1"}},
      addEventListener(){}}, $:element, AbortController, Date,
    setTimeout:()=>1, clearTimeout(){},
    fetch:async(path, options={})=>{
      requests.push({path,options});
      if(state.handler) {const result = await state.handler(path,options); if(result) return result;}
      if(path.endsWith("/status")) return response({state:state.value});
      if(path.includes("/logs?")) return response(state.logs);
      if(path.endsWith("/tasks")) {state.value="running"; return response({state:"running",task_id:"opaque_task"},202);}
      if(path.endsWith("/stop")) {state.value="stopping"; return response({state:"stopping"},202);}
      throw Error("Unexpected request");
    },
  });
  vm.runInContext(source,context);
  context.plannerEnabled=true;
  context.plannerState="idle";
  return {context,state,element,response,run:code=>vm.runInContext(code,context)};
}

test("disabled mode has no network calls and disables submit",()=>{
  const p=page();
  assert.equal(p.state.calls.length,0);
  assert.equal(p.element("#planner-start").disabled,true);
  assert.match(p.element("#command-feedback").textContent,/未配置/);
});
test("exact Chinese task is sent once and acceptance is not success",async()=>{
  const p=page(); p.element("#command").value="飞到红色汽车附近";
  await Promise.all([p.run("plannerCommand()"),p.run("plannerCommand()")]);
  assert.equal(p.state.calls.filter(x=>x.path.endsWith("/tasks")).length,1);
  const req=p.state.calls.find(x=>x.path.endsWith("/tasks"));
  assert.deepEqual(JSON.parse(req.options.body),{instruction:"飞到红色汽车附近"});
  assert.equal(req.options.credentials,"omit");
  assert.match(p.element("#command-feedback").textContent,/不代表任务完成/);
  assert.match(p.context.history[0].status,/已接收/);
  assert.equal(p.element("#command").value,"");
  await settle();
});
test("idle is labelled as available, never successful",()=>{
  const p=page();p.run("renderPlanner()");
  assert.equal(p.element("#planner-status").textContent,"可接受新任务");
  p.context.scenePhase="error";p.run("renderPlanner()");
  assert.equal(p.element("#planner-start").disabled,true);
  p.context.plannerState="running";p.run("renderPlanner()");
  assert.equal(p.element("#planner-stop").disabled,false);
});
test("blank input does not submit",async()=>{
  const p=page();p.element("#command").value=" ";
  await p.run("plannerCommand()");
  assert.equal(p.state.calls.length,0);
});
test("stop works during scene failure and sends no body",async()=>{
  const p=page();p.context.scenePhase="error";p.context.plannerState="running";
  await p.run("plannerCommand(true)");
  const req=p.state.calls.find(x=>x.path.endsWith("/stop"));
  assert.equal(req.options.method,"POST");
  assert.equal(req.options.body,undefined);
  assert.match(p.element("#command-feedback").textContent,/等待实际空闲/);
  await settle();
});
test("timed out task preserves instruction, remains uncertain and never retries",async()=>{
  const p=page();p.element("#command").value="fly";
  p.state.handler=async path=>{
    if(path.endsWith("/tasks")) throw Object.assign(Error("timeout"),{name:"AbortError"});
  };
  await p.run("plannerCommand()");
  await settle();
  await p.run("pollPlannerStatus()");
  assert.equal(p.state.calls.filter(x=>x.path.endsWith("/tasks")).length,1);
  assert.equal(p.element("#command").value,"fly");
  assert.equal(p.context.history[0].status,"结果未确认");
  assert.match(p.element("#command-feedback").textContent,/不会自动重发/);
});
test("known rejection supports detail objects and arrays",async()=>{
  for(const [status,detail] of [[409,{state:"running",message:"busy"}],
      [503,{state:"connecting",message:"connecting"}],
      [422,[{loc:["body","instruction"],msg:"bad instruction"}]]]){
    const p=page();p.element("#command").value="fly";
    p.state.handler=async path=>path.endsWith("/tasks")?p.response({detail},status):undefined;
    await p.run("plannerCommand()");
    assert.equal(p.context.history[0].status,"未接收");
    assert.equal(p.element("#command").value,"fly");
    assert.match(p.element("#command-feedback").textContent,status===422?/body.instruction: bad/:/busy|connecting/);
    await settle();
  }
});
test("late status cannot overwrite task acceptance",async()=>{
  const p=page();let release;
  p.state.handler=async path=>{
    if(path.endsWith("/status")){
      await new Promise(resolve=>release=resolve);
      return p.response({state:"idle"});
    }
  };
  const old=p.run("pollPlannerStatus()");
  p.element("#command").value="fly";
  await p.run("plannerCommand()");
  release();await old;
  assert.equal(p.context.plannerState,"running");
});
test("log failure does not erase known task state",async()=>{
  const p=page();p.context.plannerState="running";
  p.state.handler=async path=>{if(path.includes("/logs?"))throw Error("log failed");};
  await p.run("pollPlannerLogs()");
  assert.equal(p.context.plannerState,"running");
  assert.match(p.element("#planner-log-status").textContent,/异常/);
});
test("log cursor, duplicate IDs, plain text and truncation",async()=>{
  const p=page();
  p.state.logs={items:[{id:1,time:"12:00:00",level:"INFO",message:"<img onerror=evil>\nline 2"}],
    next_cursor:1,truncated:true};
  await p.run("pollPlannerLogs()");
  await p.run("pollPlannerLogs()");
  assert.equal(p.state.calls[1].path,"/api/planner/logs?cursor=1");
  assert.equal((p.element("#planner-logs").textContent.match(/<img/g)||[]).length,1);
  assert.match(p.element("#planner-logs").textContent,/旧日志/);
  assert.equal(p.element("#planner-logs").innerHTML,undefined);
});
test("restart resets cursor and deduplication; empty batch still advances cursor",async()=>{
  const p=page();
  p.state.logs={items:[],next_cursor:50,truncated:false};
  await p.run("pollPlannerLogs()");
  p.state.logs={items:[],next_cursor:0,truncated:false};
  await p.run("pollPlannerLogs()");
  assert.equal(p.run("plannerCursor"),0);
  p.state.logs={items:[{id:1,time:"t",level:"INFO",message:"new process"}],next_cursor:1,truncated:false};
  await p.run("pollPlannerLogs()");
  assert.equal(p.state.calls[2].path,"/api/planner/logs?cursor=0");
  assert.match(p.element("#planner-logs").textContent,/new process/);
});
test("display is bounded and clear does not reset cursor or stop task",async()=>{
  const p=page();
  p.state.logs={items:Array.from({length:2010},(_,id)=>({id,time:"t",level:"INFO",message:"line"+id})),
    next_cursor:2010,truncated:false};
  await p.run("pollPlannerLogs()");
  assert.equal(p.run("plannerLogRows.length"),2000);
  p.element("#planner-clear").listeners.click();
  assert.equal(p.element("#planner-logs").textContent,"");
  assert.equal(p.run("plannerCursor"),2010);
  assert.equal(p.state.calls.filter(x=>x.path.endsWith("/stop")).length,0);
});
test("same stream never overlaps while status and logs fail independently",async()=>{
  const p=page();let release;
  p.state.handler=async path=>{
    if(path.includes("/logs?"))await new Promise(resolve=>release=resolve);
  };
  const a=p.run("pollPlannerLogs()");
  await p.run("pollPlannerLogs()");
  await p.run("pollPlannerStatus()");
  assert.equal(p.state.calls.filter(x=>x.path.includes("/logs?")).length,1);
  assert.equal(p.context.plannerState,"idle");
  release();await a;
});
