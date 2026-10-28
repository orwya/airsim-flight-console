# AirSim Flight Console

AirSim 的网页控制台。浏览器里有一块远程画面、三个固定场景的启停与切换、四组天气设置。

Planner 是独立的服务，负责把自然语言指令变成飞行动作。它有自己的安装、配置和模型密钥。页面直接连接 Planner，地址在天气条件框下方的“连接设置”里填写，或由 `--planner-port` 预填。

## 组成与数据流

~~~text
浏览器
 ├─ HTTP :5173 ── Flask
 │                 ├─ 场景管理 ── xpra / Xvfb / AirSim 进程
 │                 └─ 天气 ── AirSim RPC 127.0.0.1:41451
 ├─ iframe / WebSocket :10000 ── xpra :100 ── AirSim 应用窗口
 └─ HTTP http://<ip>:<planner-port> ── Planner ── AirSim RPC
~~~

三条通道各自独立，状态含义也不同：

- 画面打开，表示 xpra 客户端连上了。
- Planner 空闲，表示它能接新任务。上一个任务成功与否要看日志。
- 提交任务返回 202，表示 Planner 收下了请求。

页面同时访问 Flask 和 Planner，两者的协调发生在浏览器里。

## 功能

- 通过 xpra HTML5 客户端嵌入 AirSim 窗口，转发键鼠输入。
- 启动、切换 AirSimNH、AbandonedPark、MSBuild2018，异步显示阶段、错误和清理结果。
- 应用晴朗、雨天、雾天、雪天四组完整预设，新实例默认晴朗。
- 直连 Planner 提交指令、停止任务、查询状态、读取增量日志；当前任务显示指令原文，其他设备连接同一 Planner 后也能通过状态接口看到正在执行的完整指令。
- 输入指令后点画面或“恢复控制”可把键盘焦点交还 AirSim。

截图、视频流和按 task_id 查询结果尚未实现。

## 环境要求

- Linux，Python 3.10 以上。
- xpra、Xvfb、`ss`（iproute2）和可用的 GPU 驱动。
- 三套 AirSim 程序已安装，二进制有执行权限：

~~~text
/data/airsim/<场景名>/LinuxNoEditor/<场景名>/Binaries/Linux/<场景名>
~~~

源配置默认读 `/data/airsim/settings.json`。RPC 端口取自该文件的 `ApiServerPort`，缺省 41451。控制台把这份配置路径原样交给 AirSim。需要启用的 RPC 请在文件里自行打开。

自然语言任务还需要一个兼容的 Planner 在运行。Planner 要监听浏览器能访问的地址，并允许控制台来源的跨域请求，包括 `POST` 的预检。页面走 https 时访问不到 http 的 Planner。

## 安装与启动

~~~bash
git clone https://github.com/orwya/airsim-flight-console.git
cd airsim-flight-console
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
~~~

第三方依赖只有 Flask 和 msgpack。

只启动画面、场景和天气：

~~~bash
.venv/bin/python app.py --ip=172.27.137.174
~~~

同时给页面预填 Planner 地址：

~~~bash
.venv/bin/python app.py --ip=172.27.137.174 --planner-port=8080
~~~

页面里的地址由 `--ip` 加 `--planner-port` 拼出，所以 `--ip` 必须是浏览器能访问的服务器地址。把示例 IP 换成实际值，然后访问 `http://172.27.137.174:5173/`。选场景后等它变成“场景已就绪”，不需要事先手动起 xpra。

省略 `--planner-port` 时页面不预填地址，可以在天气条件框下方的“连接设置”里填。Planner 状态与场景、天气互不影响。

启动会清理确认属于本项目的残留会话，正常退出也会清理。不要同时跑第二个控制台进程。

## 使用方法

1. 在环境控制里选场景，等“场景已就绪”。
2. 设置天气。新实例由后端初始化为晴朗；打开或刷新页面只读取当前天气，不会修改场景天气。
3. 点天气条件框下方的“连接设置”填 Planner 地址。状态显示“可接受新任务”后，输入完整指令，例如“飞到右前方第一辆红色汽车附近”，点“开始执行”。
4. 进度看状态和日志。running 包含解析、等模型、规划和飞行；idle 只表示能接新任务。
5. 中途中止点“停止当前任务”，等 Planner 回到 idle。关闭或刷新页面、清空日志都不会停止任务。
6. Planner 正在控制飞行时，切换场景前会弹确认；其他情况直接切换。
7. 点画面或“恢复控制”可把键盘焦点交还 AirSim。这一步不会取消 Planner，手动操作可能干扰自动任务。

能听懂什么由 Planner 版本决定。解析失败或不支持的动作可能在返回 202 之后才被拒，原因写在日志里。网络错误或超时后结果算未确认，页面保留输入并且不自动重发。

### 观察视角按键

先点画面取得焦点，再按 M 进入手动相机模式。

| 按键 | 默认作用 |
| --- | --- |
| 方向键 | 相机前后左右平移 |
| W / S | 相机抬头 / 低头 |
| A / D | 相机向左 / 向右转向 |
| Page Up / Page Down | 相机上移 / 下移 |
| 左 Shift / 左 Ctrl | 提高 / 降低相机移动速度 |
| F | 第一人称 FPV |
| B | FlyWithMe 跟随视角 |
| F1 | 当前版本的快捷键帮助 |

这些按键只移动观察相机。飞行动作由 Planner 指令决定。不同打包版本以 F1 为准。参考 [AirSim 相机文档](https://microsoft.github.io/AirSim/camera_views/)。

## 配置

配置只走命令行，不读旧版的 `SCENE_*` 等环境变量。

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| --ip | 127.0.0.1 | Flask 监听地址，浏览器与 xpra 也用它；不接受 0.0.0.0 或 :: |
| --http-port | 5173 | 控制台 HTTP 端口 |
| --xpra-port | 10000 | xpra 画面端口 |
| --display | :100 | X11 显示号 |
| --settings | /data/airsim/settings.json | AirSim 配置路径，RPC 端口从这里读 |
| --planner-port | 未配置 | Planner 端口，用于拼出页面预填地址；后端不连接它 |
| --airsim-timeout | 5 秒 | 天气操作总超时；新实例的晴朗初始化单独用 30 秒 |
| --port-release-wait | 10 秒 | 正常停止后等端口释放的上限 |
| --kill-wait | 5 秒 | 核验强杀后等端口释放的上限 |
| --xpra-ready-timeout | 30 秒 | 等 xpra 端口启动的上限 |
| --rpc-ready-timeout | 120 秒 | 等 RPC 与窗口就绪的上限 |
| --poll-interval | 0.2 秒 | 后端状态检查间隔 |

控制台、xpra、RPC 三个端口必须互不相同。

浏览器保存的 xpra 地址和 Planner 地址优先于启动参数。改了端口，页面里也要改。

显示号冲突时，先确认占用的进程，再换一个空闲显示号：

~~~bash
.venv/bin/python app.py --ip=172.27.137.174 --display=:101 --planner-port=8080
~~~

## 接口

完整约定见 [HTTP API](docs/http-api.md)。

| 接口 | 用途 |
| --- | --- |
| GET /api/health | Flask 存活检查 |
| GET /api/config | 默认 xpra 地址、Planner 预填地址、weather_control_available |
| GET /api/scenes | 三个固定场景及可用性 |
| GET /api/status | 场景生命周期、实例、天气、端口状态 |
| POST /api/switch | 异步切换场景 |
| POST /api/cleanup | 异步停止并清理受管场景 |
| GET /api/airsim | 对已就绪场景做一次 RPC ping |
| POST /api/weather | 设置天气，需带 instance_id |

Planner 的四个接口不在这里。页面直接请求 Planner 的 `/api/status`、`/api/tasks`、`/api/logs`、`/api/stop`。

## 场景切换与清理

切换是完整重启：停掉旧 xpra 和 AirSim，等端口释放，起新场景，再等 RPC 和目标窗口。旧实例的飞行位置、控制状态和未保存的仿真状态都会丢。

就绪的判定顺序：核验进程归属、确认 RPC 端口、确认监听进程属于目标场景、确认画面窗口、初始化晴朗。全部通过后才公布 `ready`。

停止优先用本项目记录的 xpra socket。超时后重新核验 PID、用户、可执行文件、参数和启动时间，只清理确认属于本项目的进程树。

切换前页面会问一次 Planner 状态。只有它正在控制飞行（`running`）时才弹确认框，可以选先停止、直接切换或取消。其余状态和连不上都直接切换，连接异常只留在状态栏。

## 运行边界

- 单进程运行，关掉热重载。多实例会抢管理器锁，状态也不在多个 worker 之间共享。
- 所有浏览器共用同一份场景、天气和 Planner 任务状态。任何页面都能提交或停止任务。
- Planner 端口对操作者网络开放，没有登录鉴权。请部署在可信网络内。
- 控制台没有登录功能，只靠请求来源检查挡住浏览器发起的跨站 POST。
- 日志、xpra 会话、socket、`manager.lock` 都在项目的 `.runtime/`，该目录已被 Git 忽略。
- 项目只在 `.runtime/` 下管理自己的文件。Unreal 和 xpra 会写用户主目录和系统临时目录。
- 启动和守护由部署方负责。退出控制台会清理它管理的会话。

## 故障排查

| 现象 | 检查方式 |
| --- | --- |
| 画面端口 10000 连不上，退出码 23 | 看 `.runtime/logs/launcher.log`；显示号冲突时换一个空闲 `--display` |
| Planner 接口 404 | 看天气条件框下方的“连接设置”里的地址是否少了路径前缀 |
| 显示“Planner 未配置” | 在“连接设置”里填地址，或启动时加 `--planner-port` |
| 提示“浏览器无法访问 Planner” | 确认地址是浏览器可达的服务器 IP、Planner 已监听该端口、CORS 放行了控制台来源 |
| 返回 202 但没动 | 看 Planner 日志：解析被拒、执行失败、被停止，或还在等模型 |
| idle 但上一任务没完成 | idle 不是成功标志，看执行日志 |
| 场景卡在 initializing | 看阶段文字，以及 RPC 和窗口是否就绪 |
| 端口被陌生进程占用 | 查清来源，交给对应的人处理，或换端口 |
| 打字后按键还作用于网页 | 点画面或“恢复控制”；静态文件更新后按 Ctrl+F5 |
| 页面还是旧界面 | Ctrl+F5；改过模板要重启后端清 Jinja 缓存 |

## 验证

~~~bash
.venv/bin/python -m unittest discover -s tests -v
node --check static/app.js
node --check static/scenes.js
node --check static/planner.js
node --test tests/test_frontend.cjs tests/test_planner_frontend.cjs
~~~

部分后端用例要求三套场景程序已安装。模拟测试覆盖到接口和状态机为止，实机验收看下面的记录。

2026-10-27 的实机记录：

- 三场景六种有向切换、四组天气、进程归属和崩溃恢复通过。
- 经 5173 代理提交“飞到右前方第一辆红色汽车附近”，约 39 秒移动约 43 米，Planner 确认到达并停稳。这个代理已经删除。
- 焦点行为有前端自动测试，浏览器人工验收没做。
- 启用 Planner 后的三场景切换矩阵和严格实例重连没验收。
- 浏览器直连 Planner 的提交、停止和日志轮询没做实机验收，只有前端自动测试。

运行记录留在 `.runtime/`，不会上传日志、模型输出、截图或环境文件。

## 文档与代码

- [HTTP API](docs/http-api.md)：接口字段、状态码、Planner 直连的跨域要求。

主要模块：`app.py`（路由）、`config.py` 与 `cli.py`（配置和入口）、`environment_service.py`（场景状态机）、`scene_processes.py`（进程归属与操作）、`scene_launcher.py`（固定启动模板）、`weather_service.py`（天气 RPC）、`static/planner.js`（浏览器直连 Planner）。