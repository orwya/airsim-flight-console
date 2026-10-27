const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const vm = require("node:vm");
const scenesSource = readFileSync(
  require("node:path").join(__dirname, "../static/scenes.js"),
  "utf8",
);
const source = readFileSync(
  require("node:path").join(__dirname, "../static/app.js"),
  "utf8",
);

function consolePage({
  connected = true,
  weatherOk = true,
  sceneControl = false,
  plannerEnabled = false,
} = {}) {
  const elements = new Map();
  function element() {
    return {
      contains() {
        return false;
      },
      focus() {
        context.document.activeElement = this;
        state.focuses++;
      },
      contentWindow: {
        focus() {
          state.windowFocuses++;
        },
      },
      dataset: {},
      classList: { add() {}, remove() {} },
      scrollTop: 0,
      setAttribute() {},
      removeAttribute() {},
      listeners: {},
      addEventListener(type, callback) { this.listeners[type] = callback; },
      append() {},
      replaceChildren() {},
    };
  }
  const state = {
    connected,
    weatherOk,
    requests: [],
    focuses: 0,
    windowFocuses: 0,
    sceneStatus: {
      phase: "ready",
      scene: "airsimnh",
      instance_id: "initial",
      operation_id: "op1",
      manager_id: "manager",
      revision: 1,
      weather: "clear",
      stage: "场景已就绪",
    },
  };
  const context = vm.createContext({
    document: {
      body: {
        dataset: {
          xpraUrl: "http://example.test:10000/",
          plannerEnabled: plannerEnabled ? "true" : "false",
          sceneControl: sceneControl ? "true" : "false",
        },
      },
      hidden: false,
      querySelector(selector) {
        if (!elements.has(selector)) elements.set(selector, element());
        return elements.get(selector);
      },
      querySelectorAll() {
        return [];
      },
      createElement: element,
      addEventListener() {},
    },
    location: { protocol: "http:", origin: "http://example.test:5173" },
    localStorage: {
      getItem() {
        return null;
      },
      setItem() {},
    },
    URL,
    AbortController,
    setTimeout() {
      return 1;
    },
    clearTimeout() {},
    setInterval() {},
    fetch: async (url, options = {}) => {
      const weather = options.body
        ? JSON.parse(options.body).weather
        : undefined;
      state.requests.push({
        url,
        weather,
        body: options.body ? JSON.parse(options.body) : null,
      });
      if (url === "/api/scenes")
        return {
          ok: true,
          json: async () => ({
            scenes: [
              { id: "airsimnh", name: "AirSimNH", available: true },
              { id: "abandonedpark", name: "AbandonedPark", available: true },
              { id: "msbuild2018", name: "MSBuild2018", available: true },
            ],
          }),
        };
      if (url === "/api/status") {
        const snapshot = { ...state.sceneStatus };
        if (state.statusWait) await state.statusWait;
        return { ok: true, json: async () => snapshot };
      }
      if (url === "/api/switch" || url === "/api/cleanup") {
        state.sceneStatus = {
          ...state.sceneStatus,
          phase: "starting",
          scene: null,
          revision: state.sceneStatus.revision + 1,
          operation_id: "next-op",
          stage: "正在切换",
        };
        return { ok: true, json: async () => ({ ...state.sceneStatus }) };
      }
      if (url === "/api/weather" && state.weatherWait) await state.weatherWait;
      const ok = url === "/api/airsim" ? state.connected : state.weatherOk;
      return {
        ok,
        json: async () =>
          url === "/api/airsim"
            ? { connected: ok }
            : ok
              ? {
                  status: "applied",
                  weather,
                  ...(sceneControl
                    ? { instance_id: JSON.parse(options.body).instance_id }
                    : {}),
                }
              : { message: "RPC failed" },
      };
    },
  });
  vm.runInContext(source, context);
  if (sceneControl) vm.runInContext(scenesSource, context);
  return { state, elements, run: (code) => vm.runInContext(code, context) };
}
const settle = () => new Promise((resolve) => setImmediate(resolve));

test("first connection applies clear and selects it after RPC succeeds", async () => {
  const page = consolePage();
  await settle();
  assert.equal(page.run("selected.weather"), "clear");
  assert.equal(
    page.elements.get("#weather-status").textContent,
    "已应用：晴朗",
  );
  assert.equal(
    page.state.requests.filter((r) => r.weather === "clear").length,
    1,
  );
  await page.run("checkAirSim()");
  assert.equal(
    page.state.requests.filter((r) => r.weather === "clear").length,
    1,
  );
});

test("offline connection waits and initializes clear after recovery", async () => {
  const page = consolePage({ connected: false });
  await settle();
  assert.equal(page.run("selected.weather"), null);
  assert.equal(page.state.requests.filter((r) => r.weather).length, 0);
  page.state.connected = true;
  await page.run("checkAirSim()");
  assert.equal(page.run("selected.weather"), "clear");
});

test("failed default is not marked applied and is retried after recovery", async () => {
  const page = consolePage({ weatherOk: false });
  await settle();
  assert.equal(page.run("selected.weather"), null);
  assert.equal(page.elements.get("#weather-status").dataset.state, "error");
  page.state.weatherOk = true;
  await page.run("checkAirSim()");
  assert.equal(page.run("selected.weather"), "clear");
});

test("periodic health checks preserve manually selected rain", async () => {
  const page = consolePage();
  await settle();
  await page.run(
    "applyWeather(options.weather.find(item => item.id === 'rain'))",
  );
  page.state.connected = false;
  await page.run("checkAirSim()");
  page.state.connected = true;
  await page.run("checkAirSim()");
  assert.equal(page.run("selected.weather"), "rain");
  assert.equal(
    page.state.requests.filter((r) => r.weather === "clear").length,
    1,
  );
});

for (const weatherOk of [true, false]) {
  test(`weather switch returns input to the AirSim iframe (RPC success=${weatherOk})`, async () => {
    const page = consolePage();
    await settle();
    assert.equal(
      page.state.focuses,
      0,
      "automatic initialization must not steal focus",
    );
    const source = page.elements.get("#xpra-frame").src;
    page.state.weatherOk = weatherOk;
    page.run("document.activeElement=document.body");
    await page.run(
      "applyWeather(options.weather.find(item => item.id === 'rain'), true)",
    );
    assert.equal(page.state.focuses, 1);
    assert.equal(page.state.windowFocuses, 1);
    assert.equal(page.run("document.activeElement === $('#xpra-frame')"), true);
    assert.equal(
      page.elements.get("#xpra-frame").src,
      source,
      "weather must not reconnect xpra",
    );
  });
}

test("a pending weather request does not steal focus from the command input", async () => {
  const page = consolePage();
  await settle();
  let release;
  page.state.weatherWait = new Promise((resolve) => {
    release = resolve;
  });
  const switching = page.run(
    "applyWeather(options.weather.find(item => item.id === 'rain'), true)",
  );
  page.run("document.activeElement=$('#command')");
  release();
  await switching;
  assert.equal(page.state.focuses, 0);
  assert.equal(page.run("document.activeElement === $('#command')"), true);
});

test("resume control returns keyboard focus without restarting the stream", async () => {
  const page = consolePage();
  await settle();
  page.run("document.activeElement=$('#command'); focusAirSim(true)");
  assert.equal(page.run("document.activeElement === $('#xpra-frame')"), true);
  assert.equal(page.state.windowFocuses, 1);
});

test("managed page initializes clear against the actual instance and renders fixed scenes", async () => {
  const page = consolePage({ sceneControl: true });
  await settle();
  assert.equal(page.run("selected.scene"), "airsimnh");
  assert.equal(page.run("selected.weather"), "clear");
  assert.equal(page.elements.get("#scene-overlay").hidden, true);
  const request = page.state.requests.find((r) => r.url === "/api/weather");
  assert.equal(request.body.instance_id, "initial");
  assert.equal(
    page.run("options.scene.map(s=>s.id).join()"),
    "airsimnh,abandonedpark,msbuild2018",
  );
});

test("managed switching blocks weather and restores controls only after ready", async () => {
  const page = consolePage({ sceneControl: true });
  await settle();
  await page.run("switchScene('abandonedpark')");
  assert.equal(page.run("sceneBusy"), true);
  assert.equal(page.elements.get("#scene-overlay").hidden, false);
  const before = page.state.requests.filter(
    (r) => r.url === "/api/weather",
  ).length;
  await page.run("applyWeather(options.weather[1],true)");
  assert.equal(
    page.state.requests.filter((r) => r.url === "/api/weather").length,
    before,
  );
  page.state.sceneStatus = {
    ...page.state.sceneStatus,
    phase: "ready",
    scene: "abandonedpark",
    instance_id: "new",
    weather: "clear",
    revision: 3,
  };
  await page.run("pollScenes()");
  assert.equal(page.run("selected.scene"), "abandonedpark");
  assert.equal(page.run("sceneInstance"), "new");
  assert.equal(page.elements.get("#scene-overlay").hidden, true);
  assert.equal(
    page.state.requests.filter((r) => r.url === "/api/weather").length,
    before,
  );
});

test("managed page discards weather success from an old instance", async () => {
  const page = consolePage({ sceneControl: true });
  await settle();
  let release;
  page.state.weatherWait = new Promise((resolve) => {
    release = resolve;
  });
  const applying = page.run("applyWeather(options.weather[1],true)");
  page.run("sceneInstance='new';selected.weather=null;scenePhase='starting'");
  release();
  await applying;
  assert.equal(page.run("selected.weather"), null);
  assert.equal(page.state.focuses, 0);
});

test("late status responses cannot undo an accepted scene operation", async () => {
  const page = consolePage({ sceneControl: true });
  await settle();
  let release;
  page.state.statusWait = new Promise((resolve) => {
    release = resolve;
  });
  const stale = page.run("pollScenes()");
  await page.run("switchScene('abandonedpark')");
  release();
  await stale;
  assert.equal(page.run("sceneBusy"), true);
  assert.equal(page.elements.get("#scene-overlay").hidden, false);
});


test("Planner blocks automatic focus changes but honors explicit user focus", async () => {
  const page = consolePage({plannerEnabled: true});
  await settle();
  for (const state of ["running", "stopping", "connecting", "unknown"]) {
    page.run("plannerState = '" + state + "'; focusAirSim()");
  }
  assert.equal(page.state.focuses, 0);
  for (const state of ["running", "stopping", "connecting", "unknown"]) {
    page.run("plannerState = '" + state + "'; focusAirSim(true)");
  }
  assert.equal(page.state.focuses, 4);
});

test("typing keeps input focus; clicking the picture explicitly returns keyboard focus", async () => {
  const page = consolePage({plannerEnabled: true});
  await settle();
  const requestsBefore = page.state.requests.length;
  page.run("plannerState='running'; document.activeElement=$('#command'); $('#command').listeners.focus()");
  assert.equal(page.state.focuses, 0);
  assert.equal(page.run("frameFocusTarget.hidden"), false);
  assert.equal(page.run("document.activeElement === $('#command')"), true);
  page.run("frameFocusTarget.listeners.pointerdown({button:0, preventDefault(){}})");
  assert.equal(page.state.focuses, 1);
  assert.equal(page.run("document.activeElement === $('#xpra-frame')"), true);
  assert.equal(page.run("frameFocusTarget.hidden"), true);
  assert.equal(page.state.requests.length, requestsBefore, "focus must not stop Planner or send RPC");
});

test("manual focus still respects scene readiness and open dialogs", async () => {
  const page = consolePage({plannerEnabled: true, sceneControl:true});
  await settle();
  page.run("plannerState='running'; scenePhase='starting'; focusAirSim(true)");
  assert.equal(page.state.focuses, 0);
  page.run("scenePhase='ready'; modal.open=true; focusAirSim(true)");
  assert.equal(page.state.focuses, 0);
});
