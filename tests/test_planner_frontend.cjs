/* 浏览器直连 Planner：请求必须打到配置的地址，不经过控制台后端。 */
const {test} = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const {readFileSync} = require("node:fs");
const {join} = require("node:path");
const source = readFileSync(join(__dirname, "../static/planner.js"), "utf8");
const settle = () => new Promise(resolve => setImmediate(resolve));
const BASE = "http://planner.test:8080";

function page({enabled = true} = {}) {
  const elements = new Map(), requests = [];
  function node() {
    return {textContent:"", dataset:{}, children:[],
      setAttribute(){},
      append(...items){this.children.push(...items);},
      replaceChildren(...items){this.children=[...items];}};
  }
  function element(selector) {
    if (!elements.has(selector)) elements.set(selector, {
      ...node(),
      value: "", textContent: "", disabled: false, hidden: false, checked: true,
      dataset: {}, scrollHeight: 100, scrollTop: 0, clientHeight: 100,
      listeners: {}, addEventListener(type, callback) {this.listeners[type] = callback;},
    });
    return elements.get(selector);
  }
  const response = (body, status=200) => ({ok:status>=200 && status<300, status, json:async()=>body});
  const state = {value:"idle", logs:{items:[],next_cursor:0,truncated:false}, calls:requests};
  const context = vm.createContext({
    plannerEnabled:false, plannerState:"disabled", plannerBase:enabled ? BASE : "",
    DEFAULT_PLANNER_URL:enabled ? BASE : "",
    scenePhase:"ready", sceneBusy:false, weatherPending:false, history:[],
    asked:0,
    document:{hidden:false,body:{dataset:{plannerUrl:enabled ? BASE : ""}},
      addEventListener(){}, createElement:()=>node()},
    $:element, AbortController, Date, URL,
    localStorage:(()=>{const store=new Map();return {
      getItem:key=>store.has(key)?store.get(key):null,
      setItem:(key,value)=>store.set(key,value),
      removeItem:key=>store.delete(key),
    };})(),
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
  context.plannerEnabled=enabled;
  context.plannerState=enabled ? "idle" : "disabled";
  return {context,state,element,response,run:code=>vm.runInContext(code,context)};
}

test("disabled mode has no network calls and disables submit",()=>{
  const p=page({enabled:false});
  assert.equal(p.state.calls.length,0);
  assert.equal(p.element("#planner-start").disabled,true);
  assert.match(p.element("#command-feedback").textContent,/未配置/);
});
test("every request goes straight to the configured address",async()=>{
  const p=page();
  p.element("#command").value="飞到红色汽车附近";
  await p.run("plannerCommand()");
  const task=p.state.calls.find(x=>x.path.endsWith("/tasks"));
  assert.equal(task.path,BASE+"/api/tasks");
  assert.equal(task.options.credentials,"omit");
  assert.deepEqual(JSON.parse(task.options.body),{instruction:"飞到红色汽车附近"});
  p.context.plannerState="idle";
  await p.run("pollPlannerStatus()");
  await p.run("pollPlannerLogs()");
  const paths=p.state.calls.map(x=>x.path);
  assert.ok(paths.includes(BASE+"/api/status"),"status must bypass the backend");
  assert.ok(paths.includes(BASE+"/api/logs?cursor=0"),"logs must bypass the backend");
  assert.ok(paths.every(path=>path.startsWith(BASE)),"no request may target the console");
});
test("exact Chinese task is sent once and acceptance is not success",async()=>{
  const p=page(); p.element("#command").value="飞到红色汽车附近";
  await Promise.all([p.run("plannerCommand()"),p.run("plannerCommand()")]);
  assert.equal(p.state.calls.filter(x=>x.path.endsWith("/tasks")).length,1);
  assert.equal(p.element("#command-feedback").textContent,"");
  assert.match(p.element("#planner-task-id").textContent,/飞到红色汽车附近/);
  assert.doesNotMatch(p.element("#planner-task-id").textContent,/opaque_task/);
  assert.equal(p.context.history[0].status,"执行中");
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
  assert.equal(req.path,BASE+"/api/stop");
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
test("an unreachable planner is reported as a browser-side connection problem",async()=>{
  const p=page();
  p.state.handler=async()=>{throw new TypeError("Failed to fetch");};
  await p.run("pollPlannerStatus()");
  assert.equal(p.context.plannerState,"unknown");
  assert.match(p.element("#planner-status-detail").textContent,/无法访问/);
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
  assert.equal(p.state.calls[1].path,BASE+"/api/logs?cursor=1");
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
  assert.equal(p.state.calls[2].path,BASE+"/api/logs?cursor=0");
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
test("preflight lets an idle planner through without asking",async()=>{
  const p=page();
  p.run("plannerAsk = async () => { asked += 1; return 'cancel'; }");
  assert.equal(await p.run("plannerPreflight()"),true);
  assert.equal(p.context.asked,0);
});
test("preflight stays silent unless the planner is flying",async()=>{
  for(const state of ["connecting","stopping","idle"]){
    const p=page();p.state.value=state;
    p.run("plannerAsk = async () => { asked += 1; return 'cancel'; }");
    assert.equal(await p.run("plannerPreflight()"),true);
    assert.equal(p.context.asked,0);
  }
});
test("preflight asks before interrupting a running task",async()=>{
  const p=page();p.state.value="running";
  p.run("plannerAsk = async () => 'cancel'");
  assert.equal(await p.run("plannerPreflight()"),false);
  p.run("plannerAsk = async () => 'switch'");
  assert.equal(await p.run("plannerPreflight()"),true);
});
test("an unreachable planner never blocks or asks before a scene switch",async()=>{
  const p=page();
  p.state.handler=async path=>{
    if(path.endsWith("/status")) throw new TypeError("Failed to fetch");
  };
  p.run("plannerAsk = async () => { asked += 1; return 'cancel'; }");
  assert.equal(await p.run("plannerPreflight()"),true);
  assert.equal(p.context.asked,0);
});
test("an unconfigured planner never asks and never calls out",async()=>{
  const p=page({enabled:false});
  p.run("plannerAsk = async () => { asked += 1; return 'cancel'; }");
  assert.equal(await p.run("plannerPreflight()"),true);
  assert.equal(p.context.asked,0);
  assert.equal(p.state.calls.length,0);
});
test("settings reject relative, credentialed and query addresses",()=>{
  const p=page({enabled:false});
  for(const value of ["172.27.137.174:8080","ftp://host:8080","http://user:pw@host:8080",
      "http://host:8080/?cursor=1","http://host:8080/#x"]){
    assert.throws(()=>p.run("normalizePlannerBase(" + JSON.stringify(value) + ")"));
  }
  assert.equal(p.run("normalizePlannerBase(' http://172.27.137.174:8080/ ')"),
    "http://172.27.137.174:8080");
});
test("saving an address in the connection dialog starts direct requests",async()=>{
  const p=page({enabled:false});
  p.run("plannerSaveAddress('http://172.27.137.174:8080')");
  assert.equal(p.context.plannerEnabled,true);
  assert.equal(p.context.plannerBase,"http://172.27.137.174:8080");
  assert.match(p.element("#planner-test-result").textContent,/已保存/);
  await settle();
  assert.ok(p.state.calls.some(call=>call.path==="http://172.27.137.174:8080/api/status"));
});
test("clearing the saved address falls back to the startup default",()=>{
  const p=page();
  p.run("localStorage.setItem('airsim-planner-url','http://old.test:9000')");
  p.run("plannerSaveAddress('')");
  assert.equal(p.context.plannerBase,BASE);
  assert.equal(p.run("localStorage.getItem('airsim-planner-url')"),null);
});
test("an invalid address is rejected and keeps the current one",()=>{
  const p=page();
  for(const bad of ["planner.test:8080","ftp://host:8080","http://user:pw@host:8080"]){
    p.run("plannerSaveAddress(" + JSON.stringify(bad) + ")");
    assert.equal(p.context.plannerBase,BASE);
    assert.match(p.element("#planner-test-result").textContent,/http|用户名/);
  }
});
test("settings panel markup and mount hook live in planner.js",()=>{
  const p=page();
  assert.match(p.run("plannerSettingsHTML()"),/id="planner-endpoint"/);
  p.run("mountPlannerSettings()");
  assert.equal(p.element("#planner-endpoint").value,BASE);
  assert.equal(p.element("#planner-test-result").textContent,"");
  p.run("plannerEnabled=false; mountPlannerSettings()");
  assert.match(p.element("#planner-test-result").textContent,/尚未配置/);
});
test("clearing without a startup default reports an unconfigured planner",()=>{
  const p=page({enabled:false});
  p.run("plannerSaveAddress('')");
  assert.equal(p.context.plannerEnabled,false);
  assert.match(p.element("#planner-test-result").textContent,/未配置/);
});

test("instruction text remains visible after execution ends without claiming success",async()=>{
  const p=page();p.element("#command").value="上升10m";
  await p.run("plannerCommand()");await settle();
  assert.match(p.element("#planner-task-id").textContent,/执行中：上升10m/);
  p.state.value="idle";
  await p.run("pollPlannerStatus()");
  assert.equal(p.context.history[0].command,"上升10m");
  assert.equal(p.context.history[0].status,"执行已结束");
  assert.equal(p.element("#planner-task-id").textContent,"");
  assert.doesNotMatch(p.context.history[0].status,/成功/);
  p.element("#command").value="向前飞行2米";
  await p.run("plannerCommand()");await settle();
  assert.equal(p.context.history.length,2);
  assert.equal(p.context.history[1].command,"上升10m");
});
test("a task with an uncertain submission is not marked executed by idle",async()=>{
  const p=page();p.element("#command").value="上升10m";
  p.state.handler=async path=>{if(path.endsWith("/tasks"))throw new TypeError("network");};
  await p.run("plannerCommand()");await settle();
  p.state.handler=undefined;p.state.value="idle";
  await p.run("pollPlannerStatus()");
  assert.equal(p.context.history[0].status,"结果未确认");
});
test("an external task does not reuse the previous instruction",async()=>{
  const p=page();p.element("#command").value="上升10m";
  await p.run("plannerCommand()");await settle();
  p.state.value="idle";await p.run("pollPlannerStatus()");
  p.state.value="running";await p.run("pollPlannerStatus()");
  assert.match(p.element("#planner-task-id").textContent,/内容未知/);
  assert.doesNotMatch(p.element("#planner-task-id").textContent,/上升10m/);
});
test("switching Planner addresses keeps history but removes the active association",async()=>{
  const p=page();p.element("#command").value="上升10m";
  await p.run("plannerCommand()");await settle();
  p.run("applyPlannerBase('http://another.test:8080')");
  assert.equal(p.context.history[0].command,"上升10m");
  assert.equal(p.context.history[0].status,"连接已切换，状态未确认");
  assert.equal(p.run("plannerActiveRecord"),null);
  await settle();
});
test("current instruction uses text and clearing logs preserves task state",async()=>{
  const p=page();p.element("#command").value="<img src=x onerror=alert(1)>";
  await p.run("plannerCommand()");await settle();
  assert.equal(p.element("#planner-task-id").textContent,"执行中：<img src=x onerror=alert(1)>");
  assert.equal(p.element("#planner-task-id").innerHTML,undefined);
  p.element("#planner-clear").listeners.click();
  assert.equal(p.context.history.length,1);
});
