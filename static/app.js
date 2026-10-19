const icons = {
  dashboard:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  route:'<circle cx="5" cy="5" r="2"/><circle cx="19" cy="19" r="2"/><path d="M7 5h8a4 4 0 0 1 0 8H9a4 4 0 0 0 0 8h5"/>',
  folder:'<path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/><path d="M3 9h18"/>',
  help:'<circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 1 1 4 3c-1 .5-1 1-1 2m0 3h.01"/>',
  settings:'<path d="m9 3-1 3-3 1 1 3-2 2 2 2-1 3 3 1 1 3h6l1-3 3-1-1-3 2-2-2-2 1-3-3-1-1-3Z"/><circle cx="12" cy="12" r="3"/>',
  link:'<path d="m10 7 2-2a5 5 0 0 1 7 7l-2 2m-3 3-2 2a5 5 0 0 1-7-7l2-2m2 5 6-6"/>',
  signal:'<path d="M4 20v-3m5 3v-7m5 7V9m5 11V4"/>',
  navigation:'<path d="m12 3 8 18-8-4-8 4Z"/>',
  camera:'<path d="m8 6 2-3h4l2 3h4a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2Z"/><circle cx="12" cy="13" r="4"/>',
  focus:'<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/><circle cx="12" cy="12" r="3"/>',
  expand:'<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M3 3l6 6m12-6-6 6M3 21l6-6m12 6-6-6"/>',
  sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
  cloud:'<path d="M6 18a5 5 0 1 1 1-10 6 6 0 0 1 11 2 4 4 0 1 1 0 8Z"/>',
  rain:'<path d="M5 15a4 4 0 1 1 2-8 5 5 0 0 1 10 1 4 4 0 0 1 2 7M8 18l-1 3m6-3-1 3m6-3-1 3"/>',
  fog:'<path d="M4 9h16M2 13h14m3 0h3M5 17h14M8 5h8"/>',
  terminal:'<path d="m4 7 5 5-5 5m8 0h8"/>',
  arrow:'<path d="M4 12h16m-6-6 6 6-6 6"/>',
  shield:'<path d="M12 2 3 6v6c0 5 9 10 9 10s9-5 9-10V6Z"/><path d="m8 12 3 3 5-6"/>',
  sliders:'<path d="M5 3v5m0 4v9M12 3v10m0 4v4M19 3v3m0 4v11M2 8h6m1 9h6m1-11h6"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/>',
  close:'<path d="m6 6 12 12M6 18 18 6"/>'
};
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.info}</svg>`;
const renderIcons = (root = document) => root.querySelectorAll('[data-icon]').forEach(el => el.innerHTML = icon(el.dataset.icon));
renderIcons();
const $ = s => document.querySelector(s);
const viewport = $('#viewport');
let toastTimer;
function toast(message) { $('#toast').textContent = message; $('#toast').classList.add('visible'); clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').classList.remove('visible'), 3600); }
const DEFAULT_XPRA_URL = document.body.dataset.xpraUrl;
let remoteActive = false;
let activeXpraUrl = '';
let connectionTimer;
function normalizeXpraUrl(value) {
  const text=value.trim();
  if(!text) throw new Error('请输入 xpra 网页地址');
  const url=new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(text)?text:'http://'+text);
  if(!['http:','https:'].includes(url.protocol)) throw new Error('请输入 http 或 https 网页地址，不能填写 WebSocket 地址');
  if(url.username || url.password || url.searchParams.has('password') || url.searchParams.has('token')) throw new Error('请勿在地址中保存凭据；如需认证，请使用 xpra 自身的登录界面');
  if(location.protocol==='https:' && url.protocol==='http:') throw new Error('当前网页使用 HTTPS，需使用 HTTPS 的 xpra 地址，或在本地 HTTP 页面中连接');
  url.hash='';
  return url;
}
function connectXpra(value, remember=true) {
  const base=normalizeXpraUrl(value);
  const url=new URL(base);
  // Xpra rejects steal=false before checking sharing. Require server sharing=yes for concurrent clients.
  Object.entries({floating_menu:'false',border:'no',clipboard:'false',printing:'false',file_transfer:'false',sound:'false',keyboard:'false',steal:'true',sharing:'true'}).forEach(([key,value])=>url.searchParams.set(key,value));
  if(remember) {try {localStorage.setItem('aerolyte-xpra-url',base.href);} catch {toast('浏览器未允许保存地址，本次连接仍可使用');}}
  activeXpraUrl=base.href;
  remoteActive=true;
  const frame=$('#xpra-frame');
  clearTimeout(connectionTimer);
  viewport.classList.add('remote-mode'); frame.hidden=false;
  $('#view-mode-label').textContent='xpra 远程窗口';
  $('#connect-label').textContent='连接设置';
  $('#feed-status').textContent='正在打开 xpra 客户端…';
  $('#open-feed').href=url.href;
  $('.feed-actions').hidden=false;
  frame.src=url.href;
  connectionTimer=setTimeout(()=>{
    if(remoteActive) $('#feed-status').textContent='若无画面，请独立打开检查连接';
  },15000);
}
function disconnectXpra() {
  remoteActive=false; clearTimeout(connectionTimer);
  $('#xpra-frame').hidden=true; $('#xpra-frame').removeAttribute('src');
  viewport.classList.remove('remote-mode');
  $('#view-mode-label').textContent='演示预览'; $('#feed-status').textContent='本地演示 · 未连接远程窗口';
  $('#connect-label').textContent='连接 AirSim'; $('.feed-actions').hidden=true;
}
$('#xpra-frame').addEventListener('load',()=>{
  if(!remoteActive) return;
  // A cross-origin iframe load does not verify the WebSocket or the AirSim session.
  $('#feed-status').textContent='xpra 嵌入模式 · 连接结果以画面为准';
});
$('#retry-feed').addEventListener('click',()=>{try {connectXpra(activeXpraUrl,false);} catch(error) {toast(error.message);}});
$('#disconnect-feed').addEventListener('click',disconnectXpra);
const options = {
  scene: [{id:'mountain', name:'山地探索', detail:'Alpine Valley', preset:'mountain'}, {id:'city', name:'城市巡航', detail:'Urban District', preset:'city'}, {id:'coast', name:'海岸漫游', detail:'Coastal Escape', preset:'coast'}],
  weather: [{id:'clear', name:'晴朗', detail:'Clear skies', preset:'sun'}, {id:'cloudy', name:'多云', detail:'Cloudy skies', preset:'cloud'}, {id:'rain', name:'小雨', detail:'Light rain', preset:'rain'}, {id:'fog', name:'薄雾', detail:'Gentle mist', preset:'fog'}]
};
const selected = {scene:'mountain', weather:'clear'};
function renderList(type) {
  const list = $('#'+type+'-list');
  list.replaceChildren();
  options[type].forEach(item => {
    const button=document.createElement('button');
    button.className='scene-button option-button'+(selected[type]===item.id?' selected':'');
    button.setAttribute('aria-pressed',String(selected[type]===item.id));
    button.dataset.option=item.id;
    const thumb=document.createElement('span');
    thumb.className=type==='scene'?'scene-thumb '+item.preset:'weather-symbol';
    if(type==='weather') thumb.innerHTML=icon(item.preset);
    const label=document.createElement('span');
    label.className='option-label';
    const name=document.createElement('b'); name.textContent=item.name;
    const detail=document.createElement('small'); detail.textContent=item.detail;
    label.append(name,detail);
    const indicator=document.createElement('span'); indicator.className='selection-indicator';
    button.append(thumb,label,indicator);
    button.addEventListener('click',()=>{
      selected[type]=item.id;
      if(type==='scene') {viewport.dataset.scene=item.preset; $('.landscape').setAttribute('aria-label',item.name+'原始演示画面'); if(remoteActive) toast('场景选择仅记录在界面中，远程 AirSim 场景切换接口待接入');}
      else toast('已选择「'+item.name+'」，天气效果将在模拟器接口接入后生效');
      const position=list.scrollTop; renderList(type); list.scrollTop=position;
    });
    list.append(button);
  });
  $('#'+type+'-count').textContent=String(options[type].length).padStart(2,'0')+' 项';
  document.querySelector('[data-remove="'+type+'"]').disabled=options[type].length<=1;
}
renderList('scene'); renderList('weather');
document.querySelectorAll('[data-remove]').forEach(button=>button.addEventListener('click',()=>{
  const type=button.dataset.remove;
  if(options[type].length<=1) return;
  const index=options[type].findIndex(item=>item.id===selected[type]);
  const removed=options[type].splice(index,1)[0];
  selected[type]=options[type][Math.min(index,options[type].length-1)].id;
  if(type==='scene') {const item=options.scene.find(item=>item.id===selected.scene);viewport.dataset.scene=item.preset;$('.landscape').setAttribute('aria-label',item.name+'原始演示画面');}
  renderList(type); toast('已从本次会话移除「'+removed.name+'」');
}));
document.querySelectorAll('[data-add]').forEach(button=>button.addEventListener('click',()=>openModal('add-'+button.dataset.add)));
document.querySelectorAll('[data-command]').forEach(button => button.addEventListener('click', () => { $('#command').value = button.dataset.command; $('#command').focus(); }));
const history = [];
$('#command-form').addEventListener('submit', e => { e.preventDefault(); const command = $('#command').value.trim(); if (!command) { $('#command').focus(); toast('请输入飞行任务，或选择一条快捷指令'); return; } history.unshift({ command, time: new Date().toLocaleTimeString('zh-CN', {hour12:false}) }); $('#command-feedback').textContent = `已记录「${command}」。当前为 UI 演示，指令尚未发送至无人机。`; $('#command').value = ''; });
$('#fullscreen').addEventListener('click', async () => { try { if (document.fullscreenElement) await document.exitFullscreen(); else await viewport.requestFullscreen(); } catch { toast('当前预览环境不支持全屏，请在独立浏览器中打开'); } });
const captures = [];
$('#capture').addEventListener('click', () => { if(remoteActive) {toast('远程画面截图需接入图像接口，当前无法保存真实截图');return;} captures.unshift({ scene:options.scene.find(item=>item.id===selected.scene).name, weather:options.weather.find(item=>item.id===selected.weather).name, time:new Date().toLocaleTimeString('zh-CN', {hour12:false}) }); toast('已将当前演示画面信息保存至媒体库（本次会话）'); });
const modal = $('#modal');
function openModal(type) {
  const content = $('#modal-content');
  if (type === 'add-scene' || type === 'add-weather') {
    const group=type==='add-scene'?'scene':'weather';
    const title=group==='scene'?'环境':'天气';
    $('#modal-title').textContent='添加'+title;
    content.innerHTML='<form id="add-option-form"><label class="modal-field" for="option-name">'+title+'名称</label><input class="modal-input" id="option-name" required maxlength="30" placeholder="输入'+title+'名称"/><label class="modal-field" for="option-preset">'+(group==='scene'?'演示画面模板':'天气图标')+'</label><select class="modal-input" id="option-preset"></select><p>仅添加本次会话中的 UI 选项，实际'+title+'资源将在接口接入后配置。</p><button class="modal-action" type="submit">添加'+title+'</button></form>';
    const presets=group==='scene'?[['mountain','山地画面'],['city','城市画面'],['coast','海岸画面']]:[['sun','晴朗'],['cloud','多云'],['rain','小雨'],['fog','薄雾']];
    presets.forEach(([value,text])=>{const option=document.createElement('option'); option.value=value; option.textContent=text; $('#option-preset').append(option);});
    $('#add-option-form').addEventListener('submit',e=>{
      e.preventDefault(); const name=$('#option-name').value.trim();
      if(!name) {$('#option-name').focus(); return;}
      if(options[group].some(item=>item.name===name)) {toast('列表中已有同名'+title);return;}
      const item={id:crypto.randomUUID(),name,detail:'自定义'+title+' · 演示',preset:$('#option-preset').value};
      options[group].push(item); selected[group]=item.id;
      if(group==='scene') {viewport.dataset.scene=item.preset;$('.landscape').setAttribute('aria-label',name+'原始演示画面');}
      renderList(group); modal.close(); $('#'+group+'-list').lastElementChild.scrollIntoView({block:'nearest'}); toast('已添加「'+name+'」');
    });
  }
  if (type === 'connect') {
    $('#modal-title').textContent='连接 AirSim 画面';
    content.innerHTML='<form id="xpra-connect-form"><p>嵌入服务器现有的 xpra 网页，显示 AirSim 窗口。此连接仅用于显示，飞行指令与环境控制仍需独立接口。</p><label class="modal-field" for="endpoint">xpra 网页地址</label><input class="modal-input" id="endpoint" placeholder="172.27.137.174:10000" required/><p>若画面为空或浏览器拒绝嵌入，可使用画面下方的“独立打开”检查。地址必须能从当前电脑访问。共享模式要求服务器开启 sharing=yes；可在服务器执行 xpra control :100 set-sharing yes。</p><p>当前控制台来源：<code id="console-origin"></code>。服务器的 frame-ancestors 必须允许此来源。</p><button class="modal-action" type="submit">连接画面</button></form>';
    $('#console-origin').textContent=location.origin;
    let saved=''; try {saved=localStorage.getItem('aerolyte-xpra-url') || '';} catch {}
    $('#endpoint').value=activeXpraUrl || saved || DEFAULT_XPRA_URL;
    $('#xpra-connect-form').addEventListener('submit',e=>{e.preventDefault();try {connectXpra($('#endpoint').value);modal.close();} catch(error) {toast(error.message);}});
  }
  if (type === 'missions') { $('#modal-title').textContent = '任务记录'; content.replaceChildren(); if (!history.length) content.innerHTML = `<div class="empty-state">${icon('route')}<p>还没有飞行指令</p><p>在控制台输入任务，指令记录将显示在这里。</p></div>`; else history.forEach(item => { const row = document.createElement('div'); row.className = 'media-item'; row.textContent = `${item.time} · ${item.command} · 未执行（演示）`; content.append(row); }); }
  if (type === 'media') { $('#modal-title').textContent = '媒体库'; content.replaceChildren(); if (!captures.length) content.innerHTML = `<div class="empty-state">${icon('camera')}<p>你的探索，值得被记录</p><p>点击画面上方的相机按钮，保存演示画面信息。<br>真实截图与视频将在视频流接入后提供。</p></div>`; else captures.forEach(item => { const row = document.createElement('div'); row.className = 'media-item'; row.textContent = `${item.time} · ${item.scene} · ${item.weather}（演示信息）`; content.append(row); }); }
  if (type === 'help') { $('#modal-title').textContent = '开始你的第一次探索'; content.innerHTML = '<p>01 &nbsp; 在右侧选择环境与天气；列表支持滚动，使用 + / − 添加或移除选项。</p><p>02 &nbsp; 在画面下方输入自然语言任务，按 Enter 或点击发送。</p><p>03 &nbsp; 画面仅展示原始内容，相机与全屏按钮位于画面上方。</p><p>xpra 模式显示远程 AirSim 窗口，演示模式显示场景图片。天气、场景与飞行指令尚未接入控制接口，不会执行真实飞行指令。任务与媒体信息仅保存在本次会话。</p>'; }
  if (type === 'settings') { $('#modal-title').textContent = '控制台设置'; content.innerHTML = '<p>AEROLYTE CONSOLE · v0.1</p><p>环境：本地控制台<br>画面源：xpra 网页嵌入<br>控制模式：自然语言指令<br>显示单位：米 / 米每秒</p><button class="modal-action" id="configure-connection">配置 xpra 画面连接</button>'; $('#configure-connection').addEventListener('click', () => openModal('connect')); }
  if (!modal.open) modal.showModal();
}
$('#connect').addEventListener('click', () => openModal('connect'));
document.querySelectorAll('[data-modal]').forEach(button => button.addEventListener('click', () => openModal(button.dataset.modal)));
$('#close-modal').addEventListener('click', () => modal.close());
modal.addEventListener('click', e => { if (e.target === modal) { const r = modal.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) modal.close(); } });

let initialXpraUrl=DEFAULT_XPRA_URL;
try {initialXpraUrl=localStorage.getItem('aerolyte-xpra-url') || DEFAULT_XPRA_URL;} catch {}
try {connectXpra(initialXpraUrl,false);} catch(error) {disconnectXpra();toast(error.message);}
