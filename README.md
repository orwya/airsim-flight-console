# AirSim Flight Console

在浏览器中启动、切换 AirSim 场景，并设置模拟天气的 Web 控制台。

后端是一个 Flask 服务：用 [xpra](https://github.com/Xpra-org/xpra) 在虚拟 X 屏幕上运行 Unreal 场景，把画面通过 xpra 的 HTML5 客户端嵌进网页，同时通过 AirSim 的 MessagePack RPC 设置天气。

```
浏览器
 ├─ HTTP <ip>:5173 ─────── Flask 控制台 ── RPC 127.0.0.1:41451 ── AirSim
 └─ iframe/WS <ip>:10000 ─ xpra :100 ── 虚拟屏幕 ── AirSim 应用窗口
```

`<ip>` 由启动参数 `--ip` 决定，同时用于后端访问和画面访问。

## 功能

- 三个固定场景：AirSimNH、AbandonedPark、MSBuild2018
- 异步切换（HTTP 202 + 状态轮询），带阶段进度与错误信息
- 切换完成后自动初始化晴朗天气
- 四组天气预设：晴朗、雨天、雾天、雪天
- 天气请求绑定场景实例 ID，切换期间拒绝写入
- 切换期间用遮罩拦截画面上的键鼠输入

## 环境要求

- Linux，Python 3.10+
- xpra（已在 6.5.5 验证）、Xvfb、`ss`（iproute2）
- 可用的 GPU 驱动与 GLX
- Unreal 会向 `~/.config/Epic/` 和 `~/.cache/` 写入配置与缓存，运行用户的 home 目录需可写
- 三套场景安装于 `/data/airsim/<场景名>/LinuxNoEditor/`，可执行文件位于
  `/data/airsim/<场景名>/LinuxNoEditor/<场景名>/Binaries/Linux/<场景名>`

## 安装

```bash
git clone https://github.com/orwya/airsim-flight-console.git
cd airsim-flight-console
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

依赖仅 `Flask` 与 `msgpack`。

## 启动

```bash
.venv/bin/python app.py --ip=172.27.137.174
```

浏览器打开 `http://172.27.137.174:5173/`，选择一个场景后等待状态变为「场景已就绪」。

其余参数按需覆盖，例如更换端口与配置路径：

```bash
.venv/bin/python app.py --ip=172.27.137.174 --xpra-port=10001 --settings=/opt/airsim/settings.json
```

运行时数据（日志、xpra 会话与 socket、管理器锁）写入项目内的 `.runtime/`，首次启动自动创建。

## 配置

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--ip` | `127.0.0.1` | 后端监听、浏览器访问、画面访问共用的地址。必须是具体 IP，`0.0.0.0` 会被拒绝 |
| `--http-port` | `5173` | 控制台监听端口 |
| `--xpra-port` | `10000` | 画面（xpra）端口 |
| `--display` | `:100` | xpra 显示号 |
| `--settings` | `/data/airsim/settings.json` | AirSim 配置路径；RPC 端口也由此文件决定 |
| `--airsim-timeout` | `5` | 普通天气请求超时（秒）；新实例初始化固定使用 30 秒 |
| `--port-release-wait` | `10` | 停止旧会话后等待端口释放的上限（秒） |
| `--kill-wait` | `5` | 强杀进程后等待端口释放的上限（秒） |
| `--xpra-ready-timeout` | `30` | 等待画面端口就绪的上限（秒） |
| `--rpc-ready-timeout` | `120` | 等待 RPC、窗口就绪的上限（秒） |
| `--poll-interval` | `0.2` | 端口轮询间隔（秒） |

由参数派生的值：

| 值 | 来源 |
| --- | --- |
| 控制台地址 | `http://<ip>:<http-port>/` |
| 画面地址 | `http://<ip>:<xpra-port>/` |
| AirSim RPC 地址 | `127.0.0.1:<ApiServerPort>` |

RPC 端口取自 settings.json 的 `ApiServerPort`，未配置时为 AirSim 默认的 `41451`。后端端口、画面端口、RPC 端口必须互不相同。

## HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 控制台页面 |
| GET | `/api/health` | 服务存活检查 |
| GET | `/api/config` | 控制台地址、画面地址与功能开关 |
| GET | `/api/scenes` | 场景列表及可用性 |
| GET | `/api/status` | 当前状态快照 |
| POST | `/api/switch` | 提交场景切换 |
| POST | `/api/cleanup` | 停止并清理当前会话 |
| GET | `/api/airsim` | 对就绪场景执行一次 RPC ping |
| POST | `/api/weather` | 设置天气 |

### GET /api/health

服务存活检查，不涉及场景状态。

```json
{"status": "ok", "backend": "flask"}
```

### GET /api/config

```json
{
  "console_url": "http://172.27.137.174:5173/",
  "xpra_url": "http://172.27.137.174:10000/",
  "display_mode": "xpra-iframe",
  "flight_control_available": false,
  "weather_control_available": true
}
```

`flight_control_available` 为预留字段，当前恒为 `false`。

### GET /api/scenes

```json
{
  "scenes": [
    {"id": "airsimnh", "name": "AirSimNH", "available": true},
    {"id": "abandonedpark", "name": "AbandonedPark", "available": true},
    {"id": "msbuild2018", "name": "MSBuild2018", "available": true}
  ]
}
```

`available` 表示场景可执行文件存在且具有执行权限。

### GET /api/status

```json
{
  "manager_id": "6f1c…",
  "revision": 7,
  "phase": "starting",
  "scene": null,
  "target_scene": "abandonedpark",
  "previous_scene": "airsimnh",
  "instance_id": "b0a2…",
  "operation_id": "0c93…",
  "completed_operation_id": "51ab…",
  "weather": null,
  "error": null,
  "stage": "正在启动 AbandonedPark",
  "cleanup": [{"pid": 4242, "scene": "airsimnh", "action": "xpra_stop"}],
  "port_xpra": false,
  "port_rpc": false
}
```

| 字段 | 说明 |
| --- | --- |
| `phase` | `idle` / `stopping` / `starting` / `initializing` / `ready` / `error` |
| `scene` | 当前就绪场景，非就绪时为 `null` |
| `instance_id` | 本次场景实例标识；天气请求需要携带 |
| `operation_id` | 当前操作标识 |
| `completed_operation_id` | 最近一次完成的操作标识 |
| `revision` | 状态版本号，每次变更递增 |
| `manager_id` | 后端进程标识，重启后变化 |
| `stage` | 面向用户展示的阶段或错误文案 |
| `cleanup` | 停止旧会话时实际处理的进程清单 |
| `port_xpra` / `port_rpc` | 两个端口的实时探测结果 |

`revision` 与 `manager_id` 用于前端丢弃过期响应。

### POST /api/switch

请求：

```json
{"scene_id": "abandonedpark"}
```

响应 `202`（已受理，需轮询 `/api/status`）或 `200`（目标场景已就绪，未重启），body 为最新状态快照。

| 状态码 | 条件 |
| --- | --- |
| `400` | `scene_id` 不在场景列表中 |
| `409` | 已有切换或天气操作在执行 |
| `503` | 环境错误，例如端口被未受管进程占用 |

### POST /api/cleanup

停止受管会话并清空状态，返回 `202` 与状态快照。忙碌时返回 `409`。

### GET /api/airsim

```json
{"connected": true, "message": "AirSim RPC 已连接"}
```

场景未就绪或 RPC 不通时返回 `503`。

### POST /api/weather

请求：

```json
{"weather": "rain", "instance_id": "b0a2…"}
```

`weather` 取 `clear` / `rain` / `fog` / `snow`；`instance_id` 来自 `/api/status`，用于拒绝过期实例的请求。

```json
{"weather": "rain", "status": "applied", "instance_id": "b0a2…"}
```

| 状态码 | 条件 |
| --- | --- |
| `400` | 天气名称不合法 |
| `409` | 场景未就绪、实例不匹配或操作忙碌 |
| `503` | AirSim RPC 调用失败 |

## 场景切换流程

一次切换按顺序执行：

1. 检查目标场景的可执行文件与权限
2. 查询画面端口与 RPC 端口的监听进程，核对归属后停止旧会话
   （优先 `xpra stop`；超时后重新核对进程身份并结束对应进程树）
3. 等待两个端口释放
4. 启动新的 xpra 会话，以 `--start-child` 拉起目标场景
5. 依次等待：画面端口可连接 → RPC 端口可连接且属于目标场景 → RPC ping 成功 → 目标窗口已显示并聚焦
6. 应用晴朗天气，状态置为 `ready`

本机实测一次切换约 6–8 秒。切换会重置飞行位置与仿真状态，浏览器画面需要重新连接。

进程归属通过「监听端口的进程 + 其可执行文件与 `-settings=` 参数」判定；无法确认归属时返回错误，不执行清理。

## 目录结构

```text
app.py                    Flask 装配：钩子、路由、错误处理
cli.py                    命令行参数与进程生命周期
config.py                 配置入口：Settings、校验、派生地址
environment_service.py    场景切换状态机与串行任务
scene_processes.py        端口与进程归属检查、受管停止、窗口确认
scene_launcher.py         三个场景与启停命令模板
weather_service.py        AirSim MessagePack RPC 与天气预设
templates/index.html      控制台模板
static/app.js             界面、画面连接与天气交互
static/scenes.js          场景请求、轮询与状态同步
tests/                    后端与前端测试
```

## 开发与测试

```bash
.venv/bin/python -m unittest discover -s tests -v
node --check static/app.js
node --check static/scenes.js
node tests/test_frontend.cjs
```

其中 3 个用例需要 `/data/airsim/` 下的场景可执行文件，在未安装场景的机器上会失败。

## 故障排查

| 现象 | 处理 |
| --- | --- |
| 切换卡在 `starting`，`stage` 显示「画面端口启动超时」 | 查看 `.runtime/logs/launcher.log`。若出现 `Server is already active for display 100`，删除 `/tmp/.X100-lock` 后重试 |
| 状态 `ready` 但画面空白 | 从浏览器访问 `/api/config` 返回的 `xpra_url`，确认该地址可达 |
| 卡在 `initializing` | 检查 settings 中的 `ApiServerPort` 与 `ss -ltnp` 显示的实际监听端口是否一致 |
| 提示端口属于未受管进程 | 该端口被其他进程占用；结束该进程后重试，或更换 `--xpra-port` |
| 提示另一个场景管理器正在运行 | 已有后端实例持有 `.runtime/manager.lock` |

日志位置：`.runtime/logs/launcher.log`（xpra 与应用启动输出）、`.runtime/logs/<场景>.log`（Unreal 日志）。

## 已知限制

- 单进程运行；多个实例会争用 `.runtime/manager.lock`，后来的实例在首次操作时报错
- 场景为三个固定项，增减需修改 `scene_launcher.py`
- 同一时刻只有一个受管场景，不支持并行预热
- 切换是重启整个 xpra 会话，不是地图热切换
- 所有浏览器共用一个场景，切换会影响其他打开的控制台
- 飞行指令输入、截图、视频流为占位界面，尚未接入