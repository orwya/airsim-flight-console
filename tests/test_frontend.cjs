const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const source = readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8');

function consolePage({connected = true, weatherOk = true} = {}) {
  const elements = new Map();
  function element() {
    return {dataset:{}, classList:{add(){},remove(){}}, scrollTop:0,
      setAttribute(){},removeAttribute(){},addEventListener(){},append(){},replaceChildren(){}};
  }
  const state = {connected, weatherOk, requests:[]};
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
