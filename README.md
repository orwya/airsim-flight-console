# AirSim Flight Console

基于 Flask 的 AirSim 网页控制台，使用原生 HTML、CSS 和 JavaScript 构建界面。通过 xpra 嵌入 AirSim 应用窗口，并通过独立的 AirSim RPC 接口发送天气设置。

## 当前状态

- **界面与画面接入**：包含指令输入、环境列表、固定天气列表及 xpra 连接入口。多客户端访问需要 xpra 开启共享。
- **默认天气**：每次新打开或刷新页面，首次 RPC 连接成功后自动应用晴朗，并在成功后选中“晴朗”。连接失败时等待重试；初始化完成后的定时检查不会覆盖手动选择。
- **天气控制**：已实现晴朗、雨天、雾天、雪天四组预设及 Flask 接口；2026-10-21 在服务器上通过真实 AirSim RPC 验证了全部四组预设（HTTP 200），测试结束恢复晴朗。视觉效果仍需在浏览器确认。
- **待实现**：自然语言飞行任务、场景切换、真实截图和独立无人机相机视频流。当前飞行指令仅在浏览器会话内记录，不会控制无人机。

## 连接关系

```text
浏览器 ── HTTP ── Flask 控制台（5173）
 │                   │
 │ iframe            └── MessagePack RPC ── AirSim（41451）
 └── HTTP/WebSocket ── xpra（10000）── AirSim 应用窗口
```

三个端口分别用于网页、画面传输、模拟器控制。**xpra 有画面不代表天气 RPC 已连通。** Flask 不代理 xpra 视频，也不启动或停止 AirSim。当前未实现登录鉴权，部署应限制在可信网络中，公网访问需另配认证。

## 在服务器运行

需要 Python 3.10+，以及已启动的 AirSim 和 xpra。以下假设 Flask 与 AirSim 运行在同一台服务器。

首次获取与启动：

```bash
git clone https://github.com/orwya/airsim-flight-console.git
cd airsim-flight-console
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py --host 0.0.0.0 --port 5173
```

在电脑浏览器打开 `http://服务器IP:5173/`。请确保电脑能访问 Flask 的 5173 端口和 xpra 的 10000 端口。

已有仓库更新时，在仓库目录执行：

```bash
git pull --ff-only
.venv/bin/python -m pip install -r requirements.txt
```

然后重启 Flask，并在浏览器按 Ctrl+F5 刷新。无需为更新网页而停止 xpra 或 AirSim。

Windows 本地开发可使用 `.venv\Scripts\python.exe` 替代上述 `.venv/bin/python`。内置 Flask 服务用于开发联调；正式部署应使用 WSGI 服务。天气参数的并发锁仅在单进程内生效，部署多个进程前需增加跨进程协调。

## 配置

环境变量在启动 Flask 前设置，修改后需重启进程。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `XPRA_URL` | `http://172.27.137.174:10000/` | 浏览器可访问的 xpra 网页地址 |
| `AIRSIM_HOST` | `127.0.0.1` | Flask 连接的 AirSim RPC 主机 |
| `AIRSIM_PORT` | `41451` | AirSim RPC TCP 端口 |
| `AIRSIM_TIMEOUT` | `5` | 单次天气请求的超时秒数，范围 `(0, 30]` |

例如：

```bash
XPRA_URL=http://172.27.137.174:10000/ \
AIRSIM_HOST=127.0.0.1 AIRSIM_PORT=41451 \
.venv/bin/python app.py --host 0.0.0.0 --port 5173
```

网页“连接设置”中保存的 xpra 地址优先于 `XPRA_URL`；它不影响天气接口的 `AIRSIM_HOST`。Flask 与 AirSim 不同机时，需配置 RPC 主机地址并确认相应端口可达。

### xpra 嵌入与共享

- 服务端 CSP 的 `frame-ancestors` 必须允许实际控制台来源，例如 `http://172.27.137.174:5173`。配置为 `*` 会允许任意网络来源嵌入。
- 多客户端访问时，xpra 启动参数应包含 `--sharing=yes`。网页使用 `sharing=true&steal=true`；若服务端未开启共享，新连接可能替换旧连接。
- 若提示 `session busy`，先检查运行中会话的共享设置，不要反复启动同一个显示号。支持该命令的版本可执行 `xpra control :100 set-sharing yes`。
- HTTPS 控制台需配套 HTTPS/WSS 的 xpra 服务。iframe 加载完成不代表远程会话已连接成功。

## 默认天气与交互

打开或刷新网页后，控制台检查 AirSim RPC。首次检查成功时自动发送 `clear` 预设，清除全部天气效果；收到成功响应后，“晴朗”按钮显示选中，状态显示“已应用：晴朗”。初始化期间显示等待或切换状态，失败时显示错误并在后续检查中重试，不会提前显示已应用。

每个页面只完成一次默认初始化。之后点击雨天、雾天、雪天或晴朗即可切换；每 15 秒的连接检查及断线恢复不会重置已成功选择的天气。若初始化前手动选择天气并成功，也以该选择为准。

**新打开或刷新另一个页面会再次应用晴朗。** 天气属于同一 AirSim 实例的全局状态，因此会影响其他正在查看该实例的客户端。当前不读取模拟器天气，也不跨浏览器同步选中状态。

## 切换天气后的键盘与鼠标输入

手动点击天气按钮后，控制台会在请求结束时将输入焦点返回 AirSim iframe，成功与失败都会处理，且不会重载画面连接。若等待期间正在输入飞行指令或操作其他控件，则保留当前焦点；首次自动应用晴朗也不抢占焦点。

画面下方的“恢复控制”可手动将键盘焦点返回 AirSim。鼠标视角的捕获受浏览器与模拟器限制，需要点击 AirSim 画面重新进入；键盘焦点恢复不等于自动恢复鼠标锁定。xpra 的 `keyboard=false` 仅隐藏屏幕虚拟键盘，不会禁用实体键盘。

页面已移除顶部品牌宣传、工作空间导航、模拟环境标记和宣传标题，保留连接设置及实际操作区。

## 天气接口与排查

`POST /api/weather` 接收固定天气标识：`clear`、`rain`、`fog`、`snow`。每次切换会启用天气并写入全部 8 项参数，包含用于清除旧效果的零值。页面仅在收到成功响应后更新选中状态。

在服务器上检查服务及 RPC 监听：

```bash
curl -s http://127.0.0.1:5173/api/health
ss -ltnp | grep ':41451'
```

发送一次天气请求（会修改当前仿真天气）：

```bash
curl -i http://127.0.0.1:5173/api/weather \
  -H 'Content-Type: application/json' \
  -d '{"weather":"rain"}'
```

成功返回 `{"status":"applied","weather":"rain"}`；400 表示输入错误，409 表示正在切换，503 表示 RPC 连接或调用失败。排查时保留响应正文和 Flask 日志。`/api/health` 只检查 Flask，不检查 AirSim；`GET /api/airsim` 实际发送只读 `ping`，连通返回 200 与 `connected: true`，失败返回 503 与 `connected: false`，不修改天气。页面每 15 秒刷新 RPC 状态；隐藏页面暂停检查。`/api/config` 返回默认配置与接口能力，不代表连接状态。

天气是同一 AirSim 实例的全局状态，多客户端最后一次成功操作生效。参数写入不是原子事务，中途失败可能部分生效，应重试完整预设；成功响应表示 RPC 调用完成，视觉效果仍取决于地图和材质支持。

## 开发与验证

```text
app.py                  Flask 页面、配置与天气路由
weather_service.py      天气预设及 AirSim RPC 调用
templates/index.html   页面模板
static/                 前端交互与样式
tests/                 Flask 与模拟 RPC 测试
```

```bash
.venv/bin/python -m unittest discover -s tests -v
# 可选：Node.js 18+，不需要安装 npm 依赖
node --test tests/test_frontend.cjs
node --check static/app.js
```

测试覆盖参数下发、无效输入、并发与异常处理，**使用模拟 RPC 响应，不能代替服务器实机验证**。前端测试直接运行实际 `static/app.js`，模拟 DOM 和接口，覆盖默认晴朗、连接恢复、失败重试、保留手动天气、天气切换后的焦点恢复及避免打断输入；不能代替真实浏览器渲染验证。后端仅依赖 Flask 与 msgpack，不需要安装 AirSim Python SDK。演示图片和字体分别来自 Unsplash、Google Fonts，需联网加载。

参考：[AirSim Python API](https://github.com/microsoft/AirSim/blob/main/PythonClient/airsim/client.py) · [xpra HTML5 配置](https://github.com/Xpra-org/xpra-html5/blob/master/docs/Configuration.md)

## 服务器部署与验证（2026-10-21）

- 工作目录：`/data/airsim-flight-console`。依赖、日志及测试产物均放在本目录的 `.venv/` 内。
- 控制台：`http://172.27.137.174:5173/`；应用画面：`http://172.27.137.174:10000/`。
- 复用既有 xpra `:100` / AirSimNH 进程；确认窗口标题为 `AirSimNH (64-bit Development SF_VULKAN_SM5)`，xpra 允许控制台嵌入并已启用共享。
- Flask 在后台运行，日志为 `.venv/console.log`，PID 为 `.venv/console.pid`；没有配置开机启动。
- 四组天气预设均经真实 RPC 返回成功，结果在 `.venv/integration-results.json`；13 项 Python 测试及 8 项前端回归测试通过。Python 测试日志在 `.venv/tests.log`。
- 浏览器自动化读取页面及截图超时，因此尚未完成嵌入画面、天气视觉效果和按钮交互的浏览器验收。HTTP 成功与 RPC 成功不能代替视觉验收。
