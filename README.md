# AirSim Flight Console

用于 AirSim 的 Flask 网页控制台，提供远程应用窗口、三个固定场景的启停切换、天气设置，以及可选的 Planner 自然语言任务控制。

## 功能

- 通过 xpra HTML5 客户端嵌入 AirSim 应用窗口，转发键鼠输入。
- 启动、切换 AirSimNH、AbandonedPark、MSBuild2018；异步显示阶段、错误和清理结果。
- 应用晴朗、雨天、雾天、雪天四组完整天气预设，新实例默认晴朗。
- 通过本机 Planner 提交完整指令、停止任务、查询状态和增量日志。
- 场景切换前确认 Planner 停止；天气、任务提交和场景生命周期使用服务端操作门禁。
- 输入指令后可点击画面或“恢复控制”切回键盘焦点；输入过程中不自动抢焦点。
- 控制台采用无左侧栏布局，快捷指令仅填写输入框，点击“开始执行”才发送。

真实截图、独立无人机相机视频流、结构化任务结果查询尚未接入。

## 架构

~~~text
浏览器
 ├─ HTTP :5173 ── Flask
 │                 ├─ 场景管理 ── xpra / Xvfb / AirSim 进程
 │                 ├─ 天气 ── AirSim RPC 127.0.0.1:41451
 │                 └─ /api/planner/* ── Planner HTTP 127.0.0.1:<planner-port>
 │                                           └─ AirSim RPC
 └─ iframe / WebSocket :10000 ── xpra :100 ── AirSim 应用窗口
~~~

画面、天气和 Planner 是不同通道。画面能打开不等于 Planner 已就绪，HTTP 202 也不代表任务执行成功。

## 环境要求

- Linux、Python 3.10+。
- xpra、Xvfb、ss（iproute2）及可用的 GPU 驱动；当前环境使用 xpra 6.5.5。
- 三套 AirSim 程序已安装，二进制具有执行权限：

~~~text
/data/airsim/<场景名>/LinuxNoEditor/<场景名>/Binaries/Linux/<场景名>
~~~

源配置默认读取 /data/airsim/settings.json。RPC 端口来自其 ApiServerPort 字段，缺省为 41451。控制台直接将该配置路径传给 AirSim，不生成配置副本，也不自动开启配置中被禁用的 RPC。

启用自然语言任务还需要独立安装、配置并启动兼容的 Planner。控制台不安装 Planner、不保存模型密钥，也不管理其进程。

## 安装与启动

~~~bash
git clone https://github.com/orwya/airsim-flight-console.git
cd airsim-flight-console
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
~~~

Python 第三方依赖只有 Flask 和 msgpack；Planner HTTP 代理使用标准库。

启动画面、场景和天气功能：

~~~bash
.venv/bin/python app.py --ip=172.27.137.174
~~~

如果 Planner 已监听本机 8080，则启用任务控制：

~~~bash
.venv/bin/python app.py --ip=172.27.137.174 --planner-port=8080
~~~

将示例 IP 替换为服务器实际 IP，在浏览器访问 http://172.27.137.174:5173/ 。选择场景后等待就绪；无需预先手动启动 xpra。

省略 --planner-port 时显示“Planner 未配置”，其余功能仍可使用。服务启动时会清理确认属于本项目的残留会话，正常退出也会清理；不要在运行中的任务旁启动第二个控制台进程。

## 使用方法

1. 在环境控制中选择场景，等待“场景已就绪”。
2. 设置所需天气。新实例先由后端初始化晴朗；新打开或刷新页面也会在首次连接时应用晴朗，可能影响其他页面的共享天气。
3. Planner 显示“可接受新任务”后，输入完整指令，例如“飞到右前方第一辆红色汽车附近”，点击“开始执行”。
4. 结合状态和日志查看进展。running 包括解析、模型等待、规划和飞行；idle 仅表示可以接收新任务。
5. 需要中止时点击“停止当前任务”，等待 Planner 恢复 idle。关闭、刷新页面或清空日志都不会停止任务。
6. 输入文字后，点击画面或“恢复控制”可将键盘焦点返回 AirSim。该操作不会取消 Planner 或释放其 API 控制，手动操作可能干扰自动任务。

指令支持范围由 Planner 版本决定。无法解析或不支持的动作可能在返回 202 后被拒绝，原因显示在日志中。网络错误或超时后结果可能未确认，网页保留输入且不自动重发。

### 常用观察视角按键

先点击画面获取焦点，按 M 进入手动相机模式。

| 按键 | 默认作用 |
| --- | --- |
| 方向键 | 相机前后左右平移 |
| W / S | 相机抬头 / 低头 |
| A / D | 相机向左 / 向右转向 |
| Page Up / Page Down | 相机上移 / 下移 |
| 左 Shift / 左 Ctrl | 提高 / 降低相机移动速度 |
| F | 第一人称 FPV |
| B | FlyWithMe 跟随视角 |
| F1 | 查看当前版本快捷键帮助 |

这些是观察相机键位，不是无人机飞行指令。不同打包版本以 F1 帮助为准。参考 [AirSim 官方相机文档](https://microsoft.github.io/AirSim/camera_views/)。

## 配置

配置统一通过命令行传入，不读取旧版 SCENE_* 等配置环境变量。

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| --ip | 127.0.0.1 | Flask 监听与浏览器、xpra 访问共用的具体 IP；不接受 0.0.0.0 或 :: |
| --http-port | 5173 | 控制台 HTTP 端口 |
| --xpra-port | 10000 | xpra 画面端口 |
| --display | :100 | X11 显示号 |
| --settings | /data/airsim/settings.json | AirSim 配置路径，RPC 端口从中读取 |
| --planner-port | 未配置 | 本机 Planner HTTP 端口；省略则关闭任务功能 |
| --planner-timeout | 5 秒 | Planner HTTP 请求超时 |
| --airsim-timeout | 5 秒 | 普通天气操作总超时；新实例晴朗初始化单独使用 30 秒 |
| --port-release-wait | 10 秒 | 正常停止后的端口释放等待上限 |
| --kill-wait | 5 秒 | 核验强杀后的端口释放等待上限 |
| --xpra-ready-timeout | 30 秒 | xpra 端口启动等待上限 |
| --rpc-ready-timeout | 120 秒 | RPC/窗口等待上限；也用于每次 Planner 空闲等待 |
| --poll-interval | 0.2 秒 | 后端状态检查间隔 |

控制台、xpra、RPC、Planner 端口必须互不相同。浏览器保存的 xpra 地址优先于服务端默认值，更改画面端口后需同步连接设置。

显示号发生冲突时，可确认另一个显示号空闲后指定，例如：

~~~bash
.venv/bin/python app.py --ip=172.27.137.174 --display=:101 --planner-port=8080
~~~

不要未经核验就删除其他用户的 X11 锁文件或结束其会话。

## 接口与状态

完整请求、响应和错误约定见 [HTTP API 文档](docs/http-api.md)。

| 接口 | 用途 |
| --- | --- |
| GET /api/health | Flask 存活检查 |
| GET /api/config | 默认地址和功能配置标志 |
| GET /api/scenes | 三个固定场景及可用性 |
| GET /api/status | 场景生命周期、实例、天气和端口状态 |
| POST /api/switch | 异步切换场景 |
| POST /api/cleanup | 异步停止并清理受管场景 |
| GET /api/airsim | 已就绪场景的实际 RPC ping |
| POST /api/weather | 设置天气，携带 instance_id |
| POST /api/planner/tasks | 提交 instruction |
| GET /api/planner/status | 查询 Planner 状态 |
| GET /api/planner/logs?cursor=N | 读取增量日志 |
| POST /api/planner/stop | 停止当前全局任务 |

## 生命周期和运行边界

场景切换采用完整重启 xpra/AirSim，旧实例的飞行状态会重置。后端依次核验进程归属、协调 Planner、停止旧会话、等待端口释放、启动新场景、确认 RPC 和目标窗口、映射聚焦窗口、初始化晴朗，再等待 Planner 空闲后公布 ready。

停止优先使用本项目的明确 xpra socket，超时后重新核验 PID、用户、可执行文件、参数和进程启动时间，仅清理确认归属的进程树。陌生监听不会被接管或强杀。

启用 Planner 时，已有场景必须等待其停止并空闲后才能退出。停止未确认或上游不可达会中止场景操作；旧会话在实际停止前的失败中保留。首次没有 AirSim 端口时允许 connecting 的 Planner 等待新场景启动，避免启动互相等待。

日志和 xpra 会话、socket、manager.lock 位于项目 .runtime/，已被 Git 忽略。此版本未使用 bwrap，也未重定向 HOME/XDG；Unreal/xpra 仍可能在用户主目录或系统临时目录写入文件，不能将 .runtime 理解为完整文件系统隔离。

## 验证

~~~bash
.venv/bin/python -m unittest discover -s tests -v
node --check static/app.js
node --check static/scenes.js
node --check static/planner.js
node --test tests/test_frontend.cjs tests/test_planner_frontend.cjs
~~~

后端部分用例依赖三套场景程序已经安装。模拟测试不会控制真实无人机或调用模型，不能代替实机验收。

截至 2026-10-27：

- 无 Planner 的三场景六种有向切换、四组天气，以及进程归属和崩溃恢复已完成实机验证。
- 已经由控制台 5173 代理提交“飞到右前方第一辆红色汽车附近”，记录约 39 秒内实际移动约 43 米，Planner 确认到达并停稳；用户确认网页画面可见移动。
- 焦点行为由前端自动测试覆盖；最新“任务运行时主动恢复焦点”仍需浏览器人工验收。
- 启用 Planner 后的三场景完整切换矩阵和严格实例重连仍未完成验收。

本机验证记录保存在 .runtime/，不会上传运行日志、模型输出、截图或环境文件。

## 故障排查

| 现象 | 检查方式 |
| --- | --- |
| 10000 连接失败，提示退出码 23 | 查看 .runtime/logs/launcher.log；若 X11 显示号冲突，核实归属后处理或改用空闲 --display |
| Planner 接口返回 404 | 确认运行的是本仓库新版本及正确目录；Python 代码更新后需重新启动后端 |
| 显示 Planner 未配置 | 启动时增加 --planner-port |
| 代理返回 502 / 504 | 检查本机 Planner 端口和日志；超时任务不要自动重发 |
| 202 后没有移动 | 查看 Planner 新增日志是否解析拒绝、执行失败、被停止或仍在等待模型 |
| idle 但上一次未完成 | idle 不是成功标志；查看执行日志 |
| 场景卡在 initializing | 查看阶段文字、RPC/窗口和 Planner 连接准备情况 |
| 端口属于未受管进程 | 检查进程来源，不要直接强杀；由对应操作者处理或更换端口 |
| 输入后按键仍作用于网页 | 点击画面或“恢复控制”；静态文件更新后 Ctrl+F5 刷新 |
| 页面仍显示旧界面 | Ctrl+F5；模板变更需在合适时机重启后端清除 Jinja 缓存 |

## 限制

- 必须单进程运行，关闭热重载；多实例会争用管理器锁，状态不在多个 WSGI worker 间共享。
- 所有浏览器共享一个场景、天气和 Planner 任务；测试页也可停止控制台提交的任务。
- Planner 没有场景实例握手或连接代次接口，重连依赖其断线检测。控制台门禁无法约束其他客户端直连 Planner/AirSim。
- 无登录鉴权；跨来源 POST 检查不等于身份认证。部署范围应为可信网络。
- 未提供开机自启或进程守护配置；退出控制台会清理其受管会话。

## 文档与代码

- [HTTP API](docs/http-api.md)：当前接口约定。
- [早期场景切换方案](docs/airsim-environment-switch-plan.md)：历史设计资料。
- [2026-10-22 实现说明](docs/airsim-flight-console-implementation.md)：历史版本说明，包含后来移除的隔离设计。

主要模块：app.py（路由）、config.py/cli.py（配置和入口）、environment_service.py（状态与操作门禁）、scene_processes.py（归属和进程操作）、scene_launcher.py（固定模板）、weather_service.py（天气 RPC）、planner_service.py（Planner HTTP）。
