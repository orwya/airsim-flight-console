/* Planner 由浏览器直连；后端不代理、不转发、不探活。 */
const PLANNER_STATUS_TIMEOUT = 5000;
const PLANNER_WRITE_TIMEOUT = 25000;
const PLANNER_POLL_INTERVAL = 1000;
const PLANNER_STOP_WAIT_MS = 20000;
const PLANNER_STOP_POLL_MS = 500;
const PLANNER_LOG_LIMIT = 2000;
const PLANNER_LOG_BOTTOM_PX = 40;
const PLANNER_STORAGE_KEY = "airsim-planner-url";
let plannerPending = false;
let plannerEpoch = 0;
let plannerStatusPending = false;
let plannerLogsPending = false;
let plannerCursor = 0;
let plannerLogsEpoch = 0;
let plannerLogRows = [];
let plannerSeenIds = new Set();
const plannerLabels = {
  disabled: "Planner 未配置", unknown: "Planner 状态未确认",
  connecting: "Planner 连接准备中", idle: "可接受新任务",
  running: "任务执行中（含解析、规划和飞行）", stopping: "正在停止任务",
};
let plannerActiveRecord = null;
let plannerRemoteInstruction = null;
const PLANNER_HISTORY_LIMIT = 100;
function updatePlannerTaskState(state) {
  if (!plannerActiveRecord || !plannerActiveRecord.accepted) return;
  if (state === "idle") {
    plannerActiveRecord.status = "执行已结束";
    plannerActiveRecord = null;
  } else if (state === "running") {
    plannerActiveRecord.status = "执行中";
  } else if (state === "stopping") {
    plannerActiveRecord.status = "正在停止";
  } else {
    plannerActiveRecord.status = "状态未确认";
  }
}
function renderPlanner() {
  $("#planner-status").textContent = plannerLabels[plannerState] || plannerLabels.unknown;
  $("#planner-status").dataset.state = plannerState;
  const active = ["running", "stopping"].includes(plannerState);
  const instruction = plannerRemoteInstruction !== null
    ? plannerRemoteInstruction : plannerActiveRecord?.accepted ? plannerActiveRecord.command : "";
  $("#planner-task-id").textContent = active
    ? instruction
      ? (plannerState === "stopping" ? "正在停止：" : "执行中：") + instruction
      : plannerRemoteInstruction === "" ? "" : "当前指令内容未知（Planner 未提供指令内容）"
    : "";
  $("#planner-start").disabled = !plannerEnabled || plannerPending ||
    plannerState !== "idle" || scenePhase !== "ready" || sceneBusy || weatherPending;
  $("#planner-stop").disabled = !plannerEnabled || plannerPending || plannerState !== "running";
}
async function plannerRequest(path, options = {}, base = plannerBase) {
  const controller = new AbortController();
  const limit = options.method === "POST" ? PLANNER_WRITE_TIMEOUT : PLANNER_STATUS_TIMEOUT;
  const timer = setTimeout(() => controller.abort(), limit);
  try {
    const response = await fetch(base + path, {
      ...options, credentials: "omit", cache: "no-store", signal: controller.signal,
    });
    const body = await response.json();
    if (!response.ok) {
      const detail = body.detail;
      const message = Array.isArray(detail)
        ? detail.map(item => (item.loc || []).join(".") + ": " + item.msg).join("\n")
        : detail?.message || (typeof detail === "string" ? detail : body.message) || "Planner 请求失败";
      const error = new Error(message);
      error.status = response.status;
      error.body = body;
      throw error;
    }
    return {body, status: response.status};
  } catch (error) {
    if (error.name === "AbortError") throw new Error("Planner 请求超时");
    // 跨域被拒和端口不可达在浏览器里都是 TypeError，只能给出合并后的提示。
    if (error.name === "TypeError")
      throw new Error("浏览器无法访问 " + base + "，请检查地址、端口和跨域设置");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
function plannerStateFrom(body) {
  if (!body || !["connecting", "idle", "running", "stopping"].includes(body.state))
    throw new Error("Planner 返回无效状态");
  return body.state;
}
async function plannerStatusSnapshot(base) {
  const {body} = await plannerRequest("/api/status", {}, base);
  const state = plannerStateFrom(body);
  if ("instruction" in body && typeof body.instruction !== "string")
    throw new Error("Planner 返回无效指令内容");
  // Older Planner versions omit instruction. Never substitute logs or task IDs.
  const instruction = !["running", "stopping"].includes(state)
    ? "" : "instruction" in body ? body.instruction : null;
  return {state, instruction};
}
async function plannerStatus(base) {
  return (await plannerStatusSnapshot(base)).state;
}
async function pollPlannerStatus() {
  if (!plannerEnabled || plannerStatusPending) return;
  plannerStatusPending = true;
  const epoch = plannerEpoch;
  try {
    if (document.hidden) return;
    const {state, instruction} = await plannerStatusSnapshot();
    if (epoch !== plannerEpoch || plannerPending) return;
    plannerState = state;
    plannerRemoteInstruction = instruction;
    if (plannerActiveRecord?.accepted && ["running", "stopping"].includes(state)
        && instruction !== null && instruction !== plannerActiveRecord.command) {
      plannerActiveRecord.status = "当前任务已变更，原任务结果见日志";
      plannerActiveRecord = null;
    }
    if (state === "connecting" && plannerActiveRecord?.accepted) {
      plannerActiveRecord.status = "连接中，原任务结果未确认";
      plannerActiveRecord = null;
    }
    updatePlannerTaskState(state);
  } catch (error) {
    if (epoch === plannerEpoch && !plannerPending) {
      plannerState = "unknown";
      plannerRemoteInstruction = null;
      updatePlannerTaskState("unknown");
      $("#planner-status-detail").textContent = "状态连接异常：" + error.message;
    }
  } finally {
    plannerStatusPending = false;
    if (plannerState !== "unknown") $("#planner-status-detail").textContent = "";
    renderPlanner();
    setTimeout(pollPlannerStatus, PLANNER_POLL_INTERVAL);
  }
}
function renderPlannerLogs() {
  const box = $("#planner-logs");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < PLANNER_LOG_BOTTOM_PX;
  box.textContent = plannerLogRows.map(row => row.text).join("\n");
  if ($("#planner-follow").checked && atBottom) box.scrollTop = box.scrollHeight;
}
function appendPlannerRows(rows) {
  plannerLogRows.push(...rows);
  plannerLogRows = plannerLogRows.slice(-PLANNER_LOG_LIMIT);
  plannerSeenIds = new Set(plannerLogRows.filter(row => row.id !== undefined).map(row => row.id));
  renderPlannerLogs();
}
async function pollPlannerLogs() {
  if (!plannerEnabled || plannerLogsPending) return;
  plannerLogsPending = true;
  const sentCursor = plannerCursor;
  const epoch = plannerLogsEpoch;
  try {
    if (document.hidden) return;
    const {body} = await plannerRequest("/api/logs?cursor=" + sentCursor);
    if (epoch !== plannerLogsEpoch) return;
    if (!Number.isSafeInteger(body.next_cursor) || body.next_cursor < 0 ||
        typeof body.truncated !== "boolean" || !Array.isArray(body.items) ||
        body.items.some(item => !Number.isSafeInteger(item.id) || item.id < 0 ||
          typeof item.time !== "string" || typeof item.level !== "string" || typeof item.message !== "string"))
      throw new Error("Planner 返回无效日志");
    if (body.next_cursor < sentCursor) {
      plannerCursor = 0;
      plannerLogsEpoch++;
      plannerSeenIds.clear();
      appendPlannerRows([{text: "[页面] 服务日志已重新编号，将从 0 读取。"}]);
      // Retained display may contain old IDs; do not reuse them in the new generation.
      plannerLogRows.forEach(row => delete row.id);
      plannerSeenIds.clear();
      return;
    }
    const rows = [];
    if (body.truncated) rows.push({text: "[页面] 部分旧日志已被服务器淘汰。"});
    for (const item of body.items) {
      if (plannerSeenIds.has(item.id)) continue;
      plannerSeenIds.add(item.id);
      rows.push({id: item.id, text: item.time + " [" + item.level + "] " + item.message});
    }
    appendPlannerRows(rows);
    plannerCursor = body.next_cursor;
    $("#planner-log-status").textContent = "日志已连接";
  } catch (error) {
    $("#planner-log-status").textContent = "日志连接异常：" + error.message;
  } finally {
    plannerLogsPending = false;
    setTimeout(pollPlannerLogs, PLANNER_POLL_INTERVAL);
  }
}
async function plannerCommand(stop = false) {
  if (!plannerEnabled || plannerPending) return;
  const instruction = $("#command").value.trim();
  if (!stop && (!instruction || plannerState !== "idle" || scenePhase !== "ready" || sceneBusy || weatherPending)) {
    $("#command-feedback").textContent = instruction ? "请等待场景就绪且 Planner 空闲。" : "请输入完整任务指令。";
    return;
  }
  if (stop && plannerState !== "running") return;
  plannerPending = true;
  plannerEpoch++;
  renderPlanner();
  const record = stop ? null : {command: instruction,
    time: new Date().toLocaleTimeString("zh-CN", {hour12: false}), status: "提交中"};
  if (record) {
    history.unshift(record);
    history.length = Math.min(history.length, PLANNER_HISTORY_LIMIT);
    plannerActiveRecord = record;
    renderPlanner();
  }
  try {
    const {body, status} = await plannerRequest(stop ? "/api/stop" : "/api/tasks", stop
      ? {method: "POST"}
      : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({instruction})});
    const nextState = plannerStateFrom(body);
    if (!stop && (status !== 202 || nextState !== "running" ||
        typeof body.task_id !== "string" || !body.task_id))
      throw new Error("任务接收响应无效");
    if (stop && ![200, 202].includes(status)) throw new Error("停止响应无效");
    plannerState = nextState;
    if (stop) {
      if (!["running", "stopping"].includes(nextState)) plannerRemoteInstruction = "";
      updatePlannerTaskState(nextState);
      $("#command-feedback").textContent = "停止请求已返回 " + nextState + "；等待实际空闲后再提交。";
    } else {
      record.taskId = body.task_id;
      record.accepted = true;
      record.status = "执行中";
      plannerRemoteInstruction = instruction;
      $("#command-feedback").textContent = "";
      if ($("#command").value.trim() === instruction) $("#command").value = "";
    }
  } catch (error) {
    const rejected = [409, 422, 503].includes(error.status);
    plannerRemoteInstruction = null;
    if (record) {
      record.status = rejected ? "未接收" : "结果未确认";
      if (rejected && plannerActiveRecord === record) plannerActiveRecord = null;
    }
    plannerState = ["connecting", "running", "stopping", "idle"].includes(error.body?.detail?.state)
      ? error.body.detail.state : "unknown";
    $("#command-feedback").textContent = error.message + "\n" +
      (rejected ? "请求未接收，请查看状态后重试。" : "请求结果未确认，不会自动重发；请先查看状态和日志。");
  } finally {
    plannerPending = false;
    plannerEpoch++;
    renderPlanner();
    pollPlannerStatus();
  }
}
function normalizePlannerBase(value) {
  const text = value.trim().replace(/\/+$/, "");
  if (!text) return "";
  let parsed;
  try {
    parsed = new URL(text);
  } catch {
    throw new Error("请输入完整地址，例如 http://172.27.137.174:8080");
  }
  if (!["http:", "https:"].includes(parsed.protocol))
    throw new Error("地址必须以 http:// 或 https:// 开头");
  if (parsed.username || parsed.password) throw new Error("地址中不要包含用户名或密码");
  if (parsed.search || parsed.hash) throw new Error("地址中不要包含查询串或片段");
  return text;
}
function applyPlannerBase(value) {
  if (value !== plannerBase && plannerActiveRecord) {
    plannerActiveRecord.status = "连接已切换，状态未确认";
    plannerActiveRecord = null;
  }
  plannerBase = value;
  plannerRemoteInstruction = null;
  plannerEnabled = Boolean(value);
  plannerState = plannerEnabled ? "unknown" : "disabled";
  plannerEpoch++;
  plannerLogsEpoch++;
  plannerPending = false;
  plannerStatusPending = false;
  plannerLogsPending = false;
  plannerCursor = 0;
  plannerSeenIds.clear();
  plannerLogRows.forEach(row => delete row.id);
  renderPlanner();
  if (plannerEnabled) {
    pollPlannerStatus();
    pollPlannerLogs();
  } else {
    showPlannerDisabledNotice();
  }
}
function showPlannerDisabledNotice() {
  $("#command-feedback").textContent = "Planner 未配置；在天气条件框下方的“连接设置”里填写 Planner 地址。";
  $("#planner-log-status").textContent = "Planner 未配置";
}
// 设置弹窗可能未打开，写入前先确认提示元素存在。
function setPlannerResult(text) {
  const box = $("#planner-test-result");
  if (box) box.textContent = text;
}
function plannerSettingsHTML() {
  return '<div class="planner-settings"><p>Planner 由页面直接连接，后端不转发。启动时加 --planner-port 会自动填入；也可在这里手动填写，Planner 的跨域设置必须允许本页来源。</p><label class="modal-field" for="planner-endpoint">Planner 地址</label><input class="modal-input" id="planner-endpoint" placeholder="http://172.27.137.174:8080"/><p class="weather-status" id="planner-test-result" role="status"></p><div class="planner-settings-actions"><button class="modal-action" type="button" id="planner-test">测试连接</button><button class="modal-action" type="button" id="planner-save">保存 Planner 地址</button></div></div>';
}
function mountPlannerSettings() {
  $("#planner-endpoint").value = plannerBase;
  setPlannerResult(plannerEnabled ? "" : "尚未配置 Planner 地址，可在这里填写后保存");
  $("#planner-test").addEventListener("click", () =>
    plannerTestAddress($("#planner-endpoint").value));
  $("#planner-save").addEventListener("click", () =>
    plannerSaveAddress($("#planner-endpoint").value));
}
async function plannerTestAddress(value) {
  setPlannerResult("正在连接…");
  try {
    const base = normalizePlannerBase(value);
    if (!base) throw new Error("请先填写地址");
    setPlannerResult("已连接，当前状态：" + (plannerLabels[await plannerStatus(base)] || "未知"));
  } catch (error) {
    setPlannerResult(error.message);
  }
}
function plannerSaveAddress(value) {
  try {
    const base = normalizePlannerBase(value);
    try {
      if (base) localStorage.setItem(PLANNER_STORAGE_KEY, base);
      else localStorage.removeItem(PLANNER_STORAGE_KEY);
    } catch {}
    const fallback = base || DEFAULT_PLANNER_URL;
    applyPlannerBase(fallback);
    setPlannerResult(
      base
        ? "已保存：" + base
        : fallback
          ? "已清除浏览器保存的地址，改用启动参数指定的地址：" + fallback
          : "已清除地址；Planner 未配置。",
    );
  } catch (error) {
    setPlannerResult(error.message);
  }
}
// 三个按钮的询问框：返回 value，关闭弹窗返回 null。测试可整体替换本函数。
function plannerAsk(message, options) {
  return new Promise(resolve => {
    $("#modal-title").textContent = "切换场景前的检查";
    const content = $("#modal-content");
    content.replaceChildren();
    const text = document.createElement("p");
    text.textContent = message;
    content.append(text);
    for (const option of options) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "modal-action";
      button.textContent = option.label;
      button.addEventListener("click", () => {
        modal.close();
        resolve(option.value);
      });
      content.append(button);
    }
    modal.addEventListener("close", () => resolve(null), { once: true });
    if (!modal.open) modal.showModal();
  });
}
async function stopThenSwitch() {
  try {
    await plannerRequest("/api/stop", { method: "POST" });
  } catch (error) {
    $("#command-feedback").textContent = "停止请求结果未确认：" + error.message;
    return false;
  }
  const deadline = Date.now() + PLANNER_STOP_WAIT_MS;
  while (Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, PLANNER_STOP_POLL_MS));
    let state;
    try {
      state = await plannerStatus();
    } catch {
      continue;
    }
    if (state === "idle") {
      plannerState = "idle";
      updatePlannerTaskState("idle");
      renderPlanner();
      return true;
    }
  }
  $("#command-feedback").textContent = "停止未确认，未切换场景。请查看执行日志。";
  return false;
}
// 后端不再检查 Planner。唯一需要确认的情况是 Planner 正在飞：切换会重置 AirSim。
async function plannerPreflight() {
  if (!plannerEnabled) return true;
  let state;
  try {
    state = await plannerStatus();
  } catch {
    // 连不上就没有正在执行的任务需要保护。连接异常只留在状态栏，不弹窗、不拦切换。
    return true;
  }
  if (state !== "running") return true;
  const choice = await plannerAsk(
    "Planner 正在控制无人机飞行。切换场景会重置 AirSim，任务随之中断。",
    [
      { value: "stop", label: "先停止任务再切换" },
      { value: "switch", label: "直接切换" },
      { value: "cancel", label: "取消" },
    ],
  );
  if (choice === "switch") return true;
  if (choice === "stop") return await stopThenSwitch();
  return false;
}
$("#command-form").addEventListener("submit", event => {
  event.preventDefault();
  plannerCommand(false);
});
$("#planner-stop").addEventListener("click", () => plannerCommand(true));
$("#planner-clear").addEventListener("click", () => {
  plannerLogRows = [];
  renderPlannerLogs();
});
$("#planner-follow").addEventListener("change", () => {
  if ($("#planner-follow").checked) $("#planner-logs").scrollTop = $("#planner-logs").scrollHeight;
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) { pollPlannerStatus(); pollPlannerLogs(); }
});
$("#command").value = "";
renderPlanner();
if (plannerEnabled) { pollPlannerStatus(); pollPlannerLogs(); }
else {
  showPlannerDisabledNotice();
}