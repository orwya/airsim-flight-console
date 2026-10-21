const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const source = readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8');

function consolePage({connected = true, weatherOk = true} = {}) {
  const elements = new Map();
  function element() {
    return {contains(){return false;}, focus(){context.document.activeElement=this; state.focuses++;},
      contentWindow:{focus(){state.windowFocuses++;}}, dataset:{}, classList:{add(){},remove(){}}, scrollTop:0,
      setAttribute(){},removeAttribute(){},addEventListener(){},append(){},replaceChildren(){}};
  }
  const state = {connected, weatherOk, requests:[], focuses:0, windowFocuses:0};
  const context = vm.createContext({
    document: {body:{dataset:{xpraUrl:'http://example.test:10000/'}}, hidden:false,
      querySelector(selector) { if (!elements.has(selector)) elements.set(selector, element()); return elements.get(selector); },
      querySelectorAll(){return [];},createElement:element,addEventListener(){}},
    location:{protocol:'http:',origin:'http://example.test:5173'},
    localStorage:{getItem(){return null;},setItem(){}},
    URL, AbortController, setTimeout(){return 1;},clearTimeout(){},setInterval(){},
    fetch:async (url, options = {}) => {
      const weather = options.body ? JSON.parse(options.body).weather : undefined;
      state.requests.push({url,weather});
      if (url === '/api/weather' && state.weatherWait) await state.weatherWait;
      const ok = url === '/api/airsim' ? state.connected : state.weatherOk;
      return {ok, json:async () => url === '/api/airsim' ? {connected:ok} : ok ? {status:'applied',weather} : {message:'RPC failed'}};
    }
  });
  vm.runInContext(source, context);
  return {state, elements, run: code => vm.runInContext(code, context)};
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('first connection applies clear and selects it after RPC succeeds', async () => {
  const page = consolePage();
  await settle();
  assert.equal(page.run('selected.weather'), 'clear');
  assert.equal(page.elements.get('#weather-status').textContent, '已应用：晴朗');
  assert.equal(page.state.requests.filter(r => r.weather === 'clear').length, 1);
  await page.run('checkAirSim()');
  assert.equal(page.state.requests.filter(r => r.weather === 'clear').length, 1);
});

test('offline connection waits and initializes clear after recovery', async () => {
  const page = consolePage({connected:false});
  await settle();
  assert.equal(page.run('selected.weather'), null);
  assert.equal(page.state.requests.filter(r => r.weather).length, 0);
  page.state.connected = true;
  await page.run('checkAirSim()');
  assert.equal(page.run('selected.weather'), 'clear');
});

test('failed default is not marked applied and is retried after recovery', async () => {
  const page = consolePage({weatherOk:false});
  await settle();
  assert.equal(page.run('selected.weather'), null);
  assert.equal(page.elements.get('#weather-status').dataset.state, 'error');
  page.state.weatherOk = true;
  await page.run('checkAirSim()');
  assert.equal(page.run('selected.weather'), 'clear');
});

test('periodic health checks preserve manually selected rain', async () => {
  const page = consolePage();
  await settle();
  await page.run("applyWeather(options.weather.find(item => item.id === 'rain'))");
  page.state.connected = false;
  await page.run('checkAirSim()');
  page.state.connected = true;
  await page.run('checkAirSim()');
  assert.equal(page.run('selected.weather'), 'rain');
  assert.equal(page.state.requests.filter(r => r.weather === 'clear').length, 1);
});

for (const weatherOk of [true, false]) {
  test(`weather switch returns input to the AirSim iframe (RPC success=${weatherOk})`, async () => {
    const page = consolePage();
    await settle();
    assert.equal(page.state.focuses, 0, 'automatic initialization must not steal focus');
    const source = page.elements.get('#xpra-frame').src;
    page.state.weatherOk = weatherOk;
    page.run('document.activeElement=document.body');
    await page.run("applyWeather(options.weather.find(item => item.id === 'rain'), true)");
    assert.equal(page.state.focuses, 1);
    assert.equal(page.state.windowFocuses, 1);
    assert.equal(page.run("document.activeElement === $('#xpra-frame')"), true);
    assert.equal(page.elements.get('#xpra-frame').src, source, 'weather must not reconnect xpra');
  });
}

test('a pending weather request does not steal focus from the command input', async () => {
  const page = consolePage();
  await settle();
  let release;
  page.state.weatherWait = new Promise(resolve => {release=resolve;});
  const switching = page.run("applyWeather(options.weather.find(item => item.id === 'rain'), true)");
  page.run("document.activeElement=$('#command')");
  release();
  await switching;
  assert.equal(page.state.focuses, 0);
  assert.equal(page.run("document.activeElement === $('#command')"), true);
});

test('resume control returns keyboard focus without restarting the stream', async () => {
  const page = consolePage();
  await settle();
  page.run("document.activeElement=$('#command'); focusAirSim(true)");
  assert.equal(page.run("document.activeElement === $('#xpra-frame')"), true);
  assert.equal(page.state.windowFocuses, 1);
});
