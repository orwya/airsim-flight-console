# AirSim Flight Console

基于 Flask 的 AirSim 网页控制台，使用原生 HTML、CSS 和 JavaScript 构建界面。通过 xpra 嵌入 AirSim 应用窗口，并通过独立的 AirSim RPC 接口发送天气设置。

## 当前状态

- **界面与画面接入**：包含指令输入、环境列表、固定天气列表及 xpra 连接入口。多客户端访问需要 xpra 开启共享。
- **天气控制**：已实现晴朗、雨天、雾天、雪天四组预设及 Flask 接口；服务器上尚未完成实际效果验证，目前仍需联调。
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

成功返回 `{"status":"applied","weather":"rain"}`；400 表示输入错误，409 表示正在切换，503 表示 RPC 连接或调用失败。排查时保留响应正文和 Flask 日志。`/api/health` 只检查 Flask，不检查 AirSim；`/api/config` 返回默认配置与接口能力，不代表连接状态。

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
```

测试覆盖参数下发、无效输入、并发与异常处理，**使用模拟 RPC 响应，不能代替服务器实机验证**。天气预设来自提供的 `weather.py`；后端仅依赖 Flask 与 msgpack，不需要安装 AirSim Python SDK。演示图片和字体分别来自 Unsplash、Google Fonts，需联网加载。

参考：[AirSim Python API](https://github.com/microsoft/AirSim/blob/main/PythonClient/airsim/client.py) · [xpra HTML5 配置](https://github.com/Xpra-org/xpra-html5/blob/master/docs/Configuration.md)
