let scenePollTimer;
// The server owns scene/instance state. Polling never treats iframe load as RPC readiness.
async function pollScenes() {
  if (scenePollPending || sceneRequestPending || document.hidden) return;
  clearTimeout(scenePollTimer);
  scenePollPending = true;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch("/api/status", {
      cache: "no-store",
      signal: controller.signal,
    });
    const state = await response.json();
    if (
      !response.ok ||
      ![
        "idle",
        "stopping",
        "starting",
        "initializing",
        "ready",
        "error",
      ].includes(state.phase)
    )
      throw new Error("无法读取有效场景状态");
    if (sceneRequestPending) return;
    if (sceneManagerId !== state.manager_id) {
      sceneManagerId = state.manager_id;
      sceneRevision = -1;
    }
    if (typeof state.revision === "number" && state.revision < sceneRevision)
      return;
    sceneRevision = state.revision ?? sceneRevision;
    scenePhase = state.phase;
    sceneBusy = ["stopping", "starting", "initializing"].includes(state.phase);
    selected.scene = state.scene;
    sceneInstance = state.instance_id;
    const text = state.error || state.stage;
    $("#scene-status").textContent = text;
    $("#scene-status").dataset.state =
      state.phase === "error" ? "error" : sceneBusy ? "pending" : "success";
    $("#scene-overlay").hidden = state.phase === "ready";
    $("#scene-overlay-text").textContent = text;
    $("#cleanup-scene").disabled = sceneBusy;
    if (state.phase === "ready") {
      $("#airsim-status").textContent = "AirSim RPC 已连接 · 场景已就绪";
      $("#airsim-status").dataset.state = "success";
      if (lastReadyInstance !== state.instance_id) {
        lastReadyInstance = state.instance_id;
        selected.weather = state.weather;
        connectXpra(activeXpraUrl || DEFAULT_XPRA_URL, false);
      }
      if (!weatherPending) {
        selected.weather = state.weather;
        const applied = options.weather.find(
          (item) => item.id === state.weather,
        );
        $("#weather-status").textContent = applied
          ? "已应用：" + applied.name
          : "天气状态未确认，请重试";
        $("#weather-status").dataset.state = applied ? "success" : "error";
      }
    } else {
      selected.weather = null;
      $("#airsim-status").textContent = sceneBusy
        ? "场景切换中，暂停天气操作"
        : "场景未就绪";
      $("#airsim-status").dataset.state = sceneBusy ? "pending" : "error";
      $("#weather-status").textContent = "新场景就绪后将初始化晴朗";
    }
    if (typeof renderPlanner === "function") renderPlanner();
    renderList("scene");
    if (!weatherPending) renderList("weather");
  } catch (error) {
    $("#scene-status").textContent = "场景状态连接失败，请检查网页服务";
    $("#scene-status").dataset.state = "error";
    scenePhase = "error";
    sceneBusy = false;
    sceneInstance = null;
    $("#scene-overlay").hidden = false;
    $("#scene-overlay-text").textContent = "状态连接失败，暂时停止画面输入";
    $("#cleanup-scene").disabled = false;
    if (typeof renderPlanner === "function") renderPlanner();
    renderList("scene");
    renderList("weather");
  } finally {
    clearTimeout(timer);
    scenePollPending = false;
    scenePollTimer = setTimeout(pollScenes, sceneBusy ? 1000 : 5000);
  }
}

async function sceneRequest(path, body) {
  if (sceneBusy || sceneRequestPending) return;
  sceneRequestPending = true;
  sceneBusy = true;
  scenePhase = "stopping";
  if (typeof renderPlanner === "function") renderPlanner();
  selected.scene = null;
  selected.weather = null;
  sceneInstance = null;
  $("#scene-overlay").hidden = false;
  $("#scene-overlay-text").textContent = "正在提交操作…";
  $("#cleanup-scene").disabled = true;
  renderList("scene");
  renderList("weather");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    const state = await response.json();
    if (!response.ok) throw new Error(state.message || "场景操作失败");
    sceneManagerId = state.manager_id;
    sceneRevision = Math.max(sceneRevision, state.revision ?? -1);
  } catch (error) {
    toast(
      error.name === "AbortError"
        ? "提交超时，正在查询服务端实际状态"
        : error.message,
    );
  } finally {
    clearTimeout(timer);
    sceneRequestPending = false;
    await pollScenes();
  }
}
// 后端不再检查 Planner。切换会重置 AirSim，所以由页面在提交前问一次。
let scenePreflightPending = false;
async function scenePreflight() {
  if (scenePreflightPending) return false;
  if (typeof plannerPreflight !== "function") return true;
  scenePreflightPending = true;
  try {
    return await plannerPreflight();
  } finally {
    scenePreflightPending = false;
  }
}
async function switchScene(id) {
  if (sceneBusy || sceneRequestPending) return;
  if (!(await scenePreflight())) return;
  return sceneRequest("/api/switch", { scene_id: id });
}
if (SCENE_CONTROL) {
  $("#cleanup-scene").addEventListener("click", async () => {
    if (sceneBusy || sceneRequestPending) return;
    if (await scenePreflight()) sceneRequest("/api/cleanup", {});
  });
  $("#scene-overlay").hidden = false;
  fetch("/api/scenes", { cache: "no-store" })
    .then((response) => {
      if (!response.ok) throw new Error("场景列表读取失败");
      return response.json();
    })
    .then((result) => {
      options.scene = result.scenes.map((item) => ({
        ...item,
        detail: item.name,
      }));
      if (typeof renderPlanner === "function") renderPlanner();
    renderList("scene");
    })
    .catch((error) => toast(error.message));
  pollScenes();
} else {
  $("#scene-status").textContent = "当前网页服务未启用场景管理";
  $("#cleanup-scene").disabled = true;
}
