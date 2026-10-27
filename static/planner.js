/* Planner state/log streams are independent. POST requests are never retried. */
let plannerPending = false;
let plannerEpoch = 0;
let plannerStatusPending = false;
let plannerLogsPending = false;
let plannerCursor = 0;
let plannerLogsEpoch = 0;
let plannerLogRows = [];
let plannerSeenIds = new Set();
let plannerLogError = "";
let plannerTaskId = "";
const plannerLabels = {
  disabled: "Planner 未配置", unknown: "Planner 状态未确认",
  connecting: "Planner 连接准备中", idle: "可接受新任务",
  running: "任务执行中（含解析、规划和飞行）", stopping: "正在停止任务",
};
const plannerTimeout = Number(document.body.dataset.plannerTimeout || 5);
function renderPlanner() {
  $("#planner-status").textContent = plannerLabels[plannerState] || plannerLabels.unknown;
  $("#planner-status").dataset.state = plannerState;
  $("#planner-task-id").textContent = plannerTaskId ? "最近接收：" + plannerTaskId : "";
  $("#planner-start").disabled = !plannerEnabled || plannerPending ||
    plannerState !== "idle" || scenePhase !== "ready" || sceneBusy || weatherPending;
  $("#planner-stop").disabled = !plannerEnabled || plannerPending || plannerState !== "running";
  $("#planner-warning").hidden = plannerState !== "running" && plannerState !== "stopping";
}
async function plannerRequest(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(),
    (options.method === "POST" ? 4 * plannerTimeout + 5 : plannerTimeout + 3) * 1000);
  try {
    const response = await fetch("/api/planner" + path, {
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
  } finally {
    clearTimeout(timer);
  }
}
function plannerStateFrom(body) {
  if (!body || !["connecting", "idle", "running", "stopping"].includes(body.state))
    throw new Error("Planner 返回无效状态");
  return body.state;
}
async function pollPlannerStatus() {
  if (!plannerEnabled || plannerStatusPending) return;
  plannerStatusPending = true;
  const epoch = plannerEpoch;
  try {
    if (document.hidden) return;
    const {body} = await plannerRequest("/status");
    if (epoch !== plannerEpoch || plannerPending) return;
    plannerState = plannerStateFrom(body);
  } catch (error) {
    if (epoch === plannerEpoch && !plannerPending) {
      plannerState = "unknown";
      $("#planner-status-detail").textContent = "状态连接异常：" + error.message;
    }
  } finally {
    plannerStatusPending = false;
    if (plannerState !== "unknown") $("#planner-status-detail").textContent = "";
    renderPlanner();
    setTimeout(pollPlannerStatus, 1000);
  }
}
function renderPlannerLogs() {
  const box = $("#planner-logs");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  box.textContent = plannerLogRows.map(row => row.text).join("\n");
  if ($("#planner-follow").checked && atBottom) box.scrollTop = box.scrollHeight;
}
function appendPlannerRows(rows) {
  plannerLogRows.push(...rows);
  plannerLogRows = plannerLogRows.slice(-2000);
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
    const {body} = await plannerRequest("/logs?cursor=" + sentCursor);
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
    plannerLogError = "";
    $("#planner-log-status").textContent = "日志已连接";
  } catch (error) {
    plannerLogError = error.message;
    $("#planner-log-status").textContent = "日志连接异常：" + plannerLogError;
  } finally {
    plannerLogsPending = false;
    setTimeout(pollPlannerLogs, 1000);
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
  if (record) history.unshift(record);
  try {
    const {body, status} = await plannerRequest(stop ? "/stop" : "/tasks", stop
      ? {method: "POST"}
      : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({instruction})});
    const nextState = plannerStateFrom(body);
    if (!stop && (status !== 202 || nextState !== "running" ||
        typeof body.task_id !== "string" || !body.task_id))
      throw new Error("任务接收响应无效");
    if (stop && ![200, 202].includes(status)) throw new Error("停止响应无效");
    plannerState = nextState;
    if (stop) {
      $("#command-feedback").textContent = "停止请求已返回 " + nextState + "；等待实际空闲后再提交。";
    } else {
      plannerTaskId = body.task_id;
      record.status = "已接收 " + body.task_id + "；结果见日志";
      $("#command-feedback").textContent = "已接收 " + body.task_id + "，不代表任务完成，请查看日志。";
      if ($("#command").value.trim() === instruction) $("#command").value = "";
    }
  } catch (error) {
    const rejected = [409, 422, 503].includes(error.status);
    if (record) record.status = rejected ? "未接收" : "结果未确认";
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
  $("#command-feedback").textContent = "Planner 未配置；启动控制台时指定 --planner-port 后启用。";
  $("#planner-log-status").textContent = "Planner 未配置";
}
