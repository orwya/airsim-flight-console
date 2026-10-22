# AirSim Flight Console

基于 Flask 的 AirSim 网页控制台。通过 xpra 显示应用窗口，通过 MessagePack RPC 修改天气，并在网页中启动、切换、停止三个固定环境。

## 已实现

- 固定环境：AirSimNH、AbandonedPark、MSBuild2018。环境列表不再是演示，已移除增删入口。
- 点击环境后异步执行：停止旧 xpra/AirSim → 等端口释放 → 启动目标会话 → 确认目标窗口与 RPC → 初始化晴朗。
- 提供切换阶段、错误和“停止并清理”；切换中遮罩画面并关闭天气写入。再次选择已就绪的同一场景不重启。
- 晴朗、雨天、雾天、雪天四组完整预设；请求关联当前实例，拒绝旧实例请求。
- 天气操作后恢复 iframe 键盘焦点；鼠标视角捕获仍需用户点击画面。

自然语言飞行任务、真实截图及独立无人机相机视频流尚未实现。输入的飞行指令只保存在本次浏览器会话，不会执行。

## 连接关系与切换影响

```text
浏览器 ── HTTP :5173 ── Flask 场景管理器
  │                       ├── RPC :41451 ── AirSim / 天气
  └── iframe / WS :10000 ── xpra :100 ── AirSim 应用窗口
planner ── RPC :41451 ───────────────────┘
```

本实现按“重启整个 xpra 会话”的方案切换，不是运行中的地图热切换。旧实例退出后，飞行位置、控制状态及未保存的仿真状态会重置，网页重新连接画面。所有浏览器共用一个环境；切换会影响其他客户端。

planner 应在连接中断后重建客户端，等待 `GET /api/status` 的 `phase=ready` 再恢复控制，并重新获取实例相关状态。端口开放不代表初始化结束；Flask 天气门禁不能阻止 planner 直接访问 RPC。

## 服务器条件

- Linux、Python 3.10+、xpra、Xvfb、ss、bwrap，以及已有 GPU 驱动。
- 当前服务器 xpra 为 6.5.5；实现使用该版本支持的 `control map/focus`。无浏览器连接时也先显示目标窗口，避免 Vulkan/GameThread 阻塞天气初始化。
- 三套应用已安装在 `/data/airsim/<环境>/LinuxNoEditor/`。
- 源 settings 为 `/data/airsim/settings.json`，只读。后端生成项目内 settings 副本，保留原配置并设置 ApiServerPort、RpcEnabled。
- 三个原始 .sh 脚本包含 chmod。模板直接调用已具备执行权限的二进制，不执行这些脚本、不修改安装目录。

首次启动：

```bash
cd /data/airsim-flight-console
python3 -m venv .venv
TMPDIR="$PWD/.venv" PIP_CACHE_DIR="$PWD/.venv/pip-cache" .venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py --host 0.0.0.0 --port 5173
```

浏览器打开 `http://172.27.137.174:5173/`。不再需要先在终端手动启动 xpra；选择环境即启动。当前服务器后台网页日志为 `.venv/console.log`，PID 记录在 `.venv/console.pid`，没有设置开机启动。

原先手动启动的 xpra/AirSim 不会被自动接管或强杀。若占用显示号或端口，先由操作者停止原会话，再从网页启动；“停止并清理”也不会越过归属检查。

## 配置

```bash
.venv/bin/python app.py --host 0.0.0.0 --port 5173 \
  --display=:100 --tcp-port=10000 --rpc-port=41451 \
  --settings=/data/airsim/settings.json \
  --port-release-wait=10 --kill-wait=5 \
  --tcp-ready-timeout=30 --rpc-ready-timeout=120 --poll-interval=0.2
```

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| --display | :100 | xpra 显示号 |
| --tcp-port | 10000 | 画面监听端口 |
| --rpc-port | 41451 | 生成 settings 和天气服务使用的统一端口 |
| --port-release-wait | 10 | 停止后等待释放；不是固定睡眠 |
| --kill-wait | 5 | 受管进程强杀后等待端口释放 |
| --tcp-ready-timeout | 30 | 画面服务启动上限 |
| --rpc-ready-timeout | 120 | RPC、归属与应用窗口就绪上限 |
| --poll-interval | 0.2 | 后端检查间隔 |
| XPRA_URL | http://172.27.137.174:10000/ | 浏览器可访问的画面地址 |
| AIRSIM_TIMEOUT | 5 | 普通天气请求超时；新实例初始化单独使用 30 秒 |

更改 tcp-port 或浏览器访问主机时，需同时设置对应的 XPRA_URL。浏览器连接设置中保存的地址优先于服务端默认地址。HTTPS 网页需配套 HTTPS/WSS xpra。

`python app.py` 自动启用场景管理。导入 `app:app` 默认只启用网页/天气，避免模块导入产生进程控制副作用；WSGI 使用场景管理时需设置 `SCENE_CONTROL_ENABLED=1`，且只允许一个服务进程。不要开启 Flask reloader 或多 worker。生产多进程部署前，应将管理器拆为独立服务。

## 模板与进程边界

`scene_launcher.py` 集中维护三个场景名、二进制路径、启动与停止参数。修改后重启后端生效。模板为可信管理员配置，返回 argv 数组，通过 shell=False 执行；浏览器只能提交 scene_id，不能提交任意命令、路径或参数。

停止前检查监听 PID、用户、实际可执行文件、精确显示号及项目运行路径。兼容 xpra 的 setproctitle 表示，不能仅凭命令里出现 :100 就认定归属。控制命令使用项目内 session socket 的明确地址，避免按显示号全局查找而连接其他会话。优先 xpra stop，超时再重新验证进程身份并处理对应进程树。端口归属未知时报告错误，不使用全局 pkill。

后端启动时清理确认属于本项目的残留会话；正常退出也执行有界清理。后台线程、天气请求和切换共用操作门禁，项目内 flock 阻止重复管理器。后端被强杀时，残留会话可由下次启动清理。

## 项目目录与文件系统隔离

所有持久运行数据位于项目内 `.runtime/`，并已加入 gitignore：

```text
.runtime/
  home/ cache/ config/ data/ run/ tmp/
  sockets/ sessions/
  settings.json       源 settings 的运行副本
  manager.lock        唯一管理器锁
  logs/launcher.log   xpra 与应用启动输出
  logs/<场景>.log     Unreal 日志
```

xpra 和 AirSim 通过 bwrap 启动：外部文件系统只读，仅项目目录可写；X11 /tmp、/dev/shm 使用隔离的临时文件系统。HOME、XDG 缓存/配置/运行目录均定向项目内。不会为了运行应用修改外部 settings、脚本、权限或用户配置。隔离不可用时拒绝启动，不能静默降级为无隔离运行。

## 接口与状态

| 接口 | 行为 |
| --- | --- |
| GET /api/health | Flask 健康，不探测场景 |
| GET /api/config | 默认画面地址及功能开关 |
| GET /api/scenes | 三个固定场景及可用性 |
| GET /api/status | phase、当前/目标场景、端口、实例、天气、阶段与错误 |
| POST /api/switch | JSON scene_id；异步任务返回 202，同一就绪场景返回 200 |
| POST /api/cleanup | 有归属检查的异步停止与清理，返回 202 |
| GET /api/airsim | 就绪场景的实际 RPC ping |
| POST /api/weather | JSON weather 和当前 instance_id |

phase 为 idle、stopping、starting、initializing、ready、error。HTTP 202 只代表已接受，需继续查询 /api/status；任务失败在 error 中报告。未知 ID 返回 400，忙碌/场景未就绪/过期实例返回 409，RPC 失败返回 503。

状态携带 operation_id、instance_id、manager_id 和 revision。前端拒绝旧版本状态与旧实例天气响应；切换中每秒查询，稳定后每 5 秒查询。状态还返回 cleanup 处理清单，包含已确认的停止对象和实际强杀 PID。状态接口只检查实时端口和生命周期；初始化阶段还额外验证 RPC、目标 PID 对应窗口及完整 clear 调用。画面是否真正可见，仍以当前浏览器实际显示为准。

天气请求示例：

```bash
curl -s http://127.0.0.1:5173/api/status
curl -s http://127.0.0.1:5173/api/switch \
  -H 'Content-Type: application/json' -d '{"scene_id":"abandonedpark"}'
# 等待 ready 后，将其 instance_id 填入天气请求：
curl -s http://127.0.0.1:5173/api/weather \
  -H 'Content-Type: application/json' -d '{"weather":"rain","instance_id":"当前实例ID"}'
```

切换失败可重试目标场景或使用“停止并清理”。清理只处理已确认归属的会话，不能用于强制结束陌生进程。没有恢复旧飞行状态或无限自动重启机制。

## 默认天气与多页面

每个新实例先由后端初始化晴朗。已打开页面切到新实例时只同步结果，不重复初始化。新打开或刷新页面仍保留“首次连接自动应用晴朗”的既有行为；因此另一个页面刷新也可能改变全局天气。后续定时检查不会把手动选择重置为晴朗。

每次天气设置写入全部八个参数，包含清除旧效果的零值。调用不是原子事务，失败可能部分生效，应重试完整预设；特殊地面效果仍取决于地图材质支持。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests -v
# Node.js 18+，无需 npm 依赖：
node --test tests/test_frontend.cjs
node --check static/app.js
node --check static/scenes.js
```

单元测试覆盖安全归属、错误重试、并发门禁、实例校验、窗口显示控制及前端迟到响应。模拟测试不能替代实机。

2026-10-22 实机已完成三个环境之间六种有向切换，每次约 6–8 秒（包含该轮四种天气验证）。各环境的完整雨、雾、雪、晴朗调用均成功，并验证了忙碌期间拒绝天气与第二次切换，以及拒绝旧实例请求。原始结果位于本机 `.runtime/integration-scenes.json`。另已验证陌生端口不误杀、应用崩溃恢复、后端强杀后重启清理、手动清理，以及实际进程的外部目录只读挂载，记录在 `.runtime/integration-faults.json`。当前 27 项后端测试、12 项前端测试通过。

浏览器自动化读取及截图持续超时，真实浏览器画面与键鼠仍需人工验收；窗口 PID、RPC、天气调用和 HTTP 画面入口已分别检查。不要将 iframe load 或 RPC 成功当作视觉与输入验收。

当前未实现登录鉴权，修改接口拒绝跨来源浏览器请求，但这不等于身份认证。仅部署在受控可信网络；公网或多用户权限场景需另加认证与访问控制。

## 文件结构

```text
app.py                    Flask、CLI、生命周期入口
scene_launcher.py         三个固定场景和 argv 模板
scene_processes.py        归属检查、隔离启动、受管停止与窗口显示
environment_service.py    串行任务、状态、天气门禁和退出清理
weather_service.py        AirSim MessagePack RPC 与天气预设
templates/index.html      控制台模板
static/app.js             通用界面、画面连接与天气
static/scenes.js          场景请求、轮询、遮罩与状态同步
tests/                    后端与前端测试
```
