# AirSim 网页环境切换实施方案

> 历史资料：本文保留早期设计和实现记录，部分内容已被后续代码替代，不能直接作为当前部署步骤。当前版本未使用 bwrap 或 HOME/XDG 重定向；场景切换采用完整重启 xpra/AirSim，并已增加 Planner 代理与生命周期协调。请以 [README](../README.md) 和 [HTTP API](http-api.md) 为准。


- 日期：2026-10-22
- 项目：`172.27.137.174:/data/airsim-flight-console`
- 状态：设计方案，尚未实现；本次没有启动或停止模拟器。

## 1. 推荐结论

网页“环境控制”固定为 **AirSimNH、AbandonedPark、MSBuild2018** 三项，移除演示场景和环境增删入口。用户点击后，由后端完成旧环境停止、新环境启动、状态检查和默认晴朗初始化。

**推荐让 xpra `:100` 常驻，只切换 AirSim 进程。** 三个环境共用显示号 `:100`、画面端口 `10000` 和 RPC 端口 `41451`，任一时刻只运行一个受管模拟器。这是重新启动另一个打包应用，不是通过 AirSim RPC 在运行中换地图，因此会重置飞行位置及仿真状态。

## 2. 已核实条件与目录约束

2026-10-22 的只读检查确认：

- xpra 版本为 `6.5.5-r0`；三个启动脚本和可执行文件均存在，可执行文件已有执行权限。
- `/data/airsim/settings.json` 存在，`SimMode=Multirotor`，未显式配置 RPC 端口。实施时仍需实测每个环境的 RPC 监听地址。
- 检查时未发现上述环境运行进程，不能沿用昨天的运行状态判断。
- 现有 Flask、天气 RPC、RPC ping 可以复用；当前环境列表仍是浏览器演示。

**三个 `.sh` 都执行了对外部二进制的 `chmod +x`。** 为遵守仅修改项目目录的要求，不直接执行原脚本，而按其实际内容调用可执行文件，保留同名项目参数。

不修改 `/data/airsim` 下的应用、脚本和 settings，不修改用户 shell、系统 xpra 配置或系统服务。新增日志、PID、锁、状态及缓存安排在项目内 `.runtime/`。

只设置 cwd 无法约束外部程序的所有写入。Unreal Saved、日志、崩溃报告、GPU 缓存，以及 xpra/X11 认证、会话文件和系统套接字，均需审计并验证重定向。优先复用用户已启动的 xpra；普通 X11 新会话可能必须创建项目外的系统运行时对象，不能默认已获授权。若严格禁止一切项目外写入，需要文件系统隔离验证；不能声称仅设置 HOME、TMPDIR 就一定满足。

## 3. 两种方法比较

| 方式 | 实现 | 优点 | 代价 | 建议 |
| --- | --- | --- | --- | --- |
| A：重启整个 xpra | 停旧会话，再使用目标命令启动 `:100` | 接近现有手工流程 | 浏览器断连；X11、xpra、应用一起重启 | 兼容性回退或 xpra 故障恢复 |
| B：xpra 常驻 | 保留 `:100`，仅替换其中的 AirSim 进程 | 通常保留画面连接；切换组件少 | 需增加进程生命周期管理 | 正常切换首选 |

原命令的 `--exit-with-children=yes` 会在最后一个 `--start-child` 退出后结束 xpra，不能照搬到方案 B。常驻会话应使用 `--exit-with-children=no`，并明确不随客户端断开退出。该生命周期关系见 [xpra 官方说明](https://github.com/Xpra-org/xpra/blob/master/docs/Usage/Security.md)。

继续使用当前应用窗口模式，无须切换为 xpra desktop。空会话保留、新窗口发现及键鼠输入要实测；若该版本不能稳定支持，再回退方案 A。

## 4. 架构与进程归属

```text
浏览器：三个固定按钮 / 任务轮询 / xpra iframe
       │ HTTP
Flask：请求校验 / 任务提交 / 状态与天气接口
       │ 本机队列
环境管理器：唯一执行者 / 生命周期锁 / 子进程与日志管理
       ├── 常驻 xpra :100 ── X11 显示
       └── 一个 AirSim 进程 ── RPC :41451
```

第一版使用单 Flask 服务进程内的单例管理器和后台工作线程。HTTP 立即返回任务 ID，不等待地图加载。禁用 Flask reloader，使用项目内进程间锁防止重复管理器；后续若使用多 WSGI worker，先将管理器拆成独立本机服务，不能每个 worker 启动一套。

通过 `subprocess.Popen` 参数数组和 `shell=False` 启动，使用独立进程组；记录 PID、PGID、启动时间和实际可执行路径。停止前验证归属，只处理本管理器的进程组。stdout/stderr 直接写日志，避免无人读取的 PIPE 堵塞。参见 [Python subprocess 文档](https://docs.python.org/3/library/subprocess.html)。

由管理器单独启动的 AirSim 不属于 xpra 的受管子命令，不能依靠 `--terminate-children=yes` 自动清理；模拟器必须由管理器回收。

## 5. 固定白名单与启动参数

| ID | 名称 | 可执行文件绝对路径 | 首个参数 |
| --- | --- | --- | --- |
| `airsimnh` | AirSimNH | `/data/airsim/AirSimNH/LinuxNoEditor/AirSimNH/Binaries/Linux/AirSimNH` | `AirSimNH` |
| `abandonedpark` | AbandonedPark | `/data/airsim/AbandonedPark/LinuxNoEditor/AbandonedPark/Binaries/Linux/AbandonedPark` | `AbandonedPark` |
| `msbuild2018` | MSBuild2018 | `/data/airsim/MSBuild2018/LinuxNoEditor/MSBuild2018/Binaries/Linux/MSBuild2018` | `MSBuild2018` |

前端只提交 ID，后端只认白名单，不接受任意路径、命令、显示号、端口或附加参数。

```python
args = [environment.executable, environment.project_name,
        "-settings=/data/airsim/settings.json"]
# Popen(args, shell=False, start_new_session=True, env=env, ...)
```

参数数组中不包含 shell 引号。子进程设置 `DISPLAY=:100` 并使用该会话实际的 XAUTHORITY，不能只设置 DISPLAY 后假定能够连接。安装目录仅用作只读资源，不通过 chmod 修复权限。

Unreal 输出路径需针对这三个旧包实测。官方有 ABSLOG 等参数，但重定向日志不等于重定向全部 Saved/缓存，不能假设新版选项对旧包全部有效。参见 [Unreal 4.27 命令行文档](https://dev.epicgames.com/documentation/es-es/unreal-engine/command-line-arguments?application_version=4.27)。
## 6. 切换流程

1. **预检**：校验 ID、文件权限、settings、xpra 和唯一管理锁。目标环境已就绪时直接返回，不重启。
2. **受理任务**：返回 `202 Accepted` 和 `operation_id`，状态进入 stopping；忙碌时返回 `409`，不排队执行过时切换。
3. **暂停控制写入**：阻止新天气请求，等待已开始请求在超时内完成。天气与生命周期共用操作协调，不能分别使用互不相干的锁。
4. **停止旧进程**：验证归属，发送 SIGTERM，建议等待 15 秒，再按需 SIGKILL 并回收。确认旧进程退出、旧 RPC 监听释放；外部进程占用端口时报告冲突，不杀外部进程。
5. **启动目标**：在原 xpra 显示中启动新程序，生成新的 `instance_id`。初始启动超时建议 120 秒，按实测调整。
6. **验证就绪**：目标进程存活、xpra 出现属于目标 PID 的窗口、预期 RPC ping 成功三项同时满足。窗口按 PID 关联，不只匹配标题；TCP 连通或 HTTP 200 单独不算成功。
7. **初始化天气**：在新实例应用完整 clear 预设，期间仍不接受外部天气写入。成功后发布 ready、实际当前环境与晴朗；失败进入明确错误状态。
8. **页面同步**：选中实际成功的环境，清除旧实例的天气与待处理响应，显示晴朗并解除操作限制。保持 iframe 地址，仅在 xpra 真正断开或新窗口未恢复时提供重连。

页面展示“停止旧环境 → 启动目标环境 → 等待 RPC → 初始化天气”等阶段，不显示虚构进度百分比。

## 7. 状态与接口

状态：`stopped`、`stopping`、`starting`、`initializing`、`ready`、`error`。任务另记阶段、时间、错误和最终结果。

| 接口 | 用途 |
| --- | --- |
| `GET /api/environments` | 三个固定选项及可用性 |
| `GET /api/environment/status` | 当前/目标环境、阶段、任务、实例、天气与分项健康状态 |
| `POST /api/environment/switch` | 接收 environment_id 和幂等请求 ID，提交任务 |
| `GET /api/environment/operations/<id>` | 查询任务，刷新后仍能恢复结果 |
| `POST /api/weather` | 保留，增加实例校验；切换中返回 `409 environment_busy` |

示例：

```json
{
  "state": "starting",
  "current_environment": null,
  "previous_environment": "airsimnh",
  "target_environment": "abandonedpark",
  "operation_id": "switch-...",
  "instance_id": "instance-...",
  "xpra_connected": true,
  "rpc_ready": false,
  "weather": null
}
```

旧环境停止后 current_environment 置空，不能继续显示它正在运行。请求和响应关联实例 ID，丢弃旧实例迟到的天气响应。运行中继续监测崩溃，不只验证启动瞬间。

## 8. 前端、多客户端与默认天气

- 固定显示三个真实名称，删除环境 +/− 和添加环境弹窗代码。
- 切换时禁用环境/天气写操作，保留任务查询；建议遮罩画面输入，防止按键作用于初始化中的新实例。
- 任务期间每 1 秒轮询，稳定后每 5 秒；刷新从后端恢复状态，不依赖本地变量。
- “环境已就绪”和“当前浏览器画面已连接”分开提示。切换完成不用整页刷新实现。
- 保留“恢复控制”；可以恢复 iframe 键盘焦点，鼠标锁定仍需用户点击画面。
- 环境为所有客户端共享的全局状态，切换影响其他观看者；各页面统一读取服务端状态。

兼容已约定的默认晴朗规则：新环境由后端初始化晴朗，已有页面识别新实例后同步状态，不重复初始化。新打开或刷新页面仍保留“首次连接应用晴朗”行为，但必须等待 ready 并携带当前实例 ID；这同样影响其他客户端。如果以后改成刷新只同步当前天气，需另行明确规则。
## 9. 失败恢复与外部进程

- 预检失败不停止旧环境，直接说明文件、权限或会话问题。
- 新进程退出、启动超时、RPC 不通或窗口缺失：清理本次受管进程，保留 xpra，显示错误和日志摘要。
- 第一版提供“重试目标”“恢复上一个环境”，不无限自动重启。恢复上个环境也是新实例，不能恢复旧飞行状态。
- xpra 崩溃单独标记；只有归属、写入范围和重建权限明确后才能重建会话。
- 管理服务重启后，结合记录、进程启动时间、实际可执行路径、窗口与 RPC 检查恢复状态，不只相信 PID 文件。
- 手工启动的 AirSim 或占用显示/端口的陌生进程不自动接管。禁止全局 pkill、按名字批量杀进程或删除外部锁文件；先明确一次性迁移步骤。
- 第一版不并行预热第二套环境，避免 GPU 和 RPC 端口竞争。

## 10. Python 模块与目录

建议新增：

```text
environment_service.py      白名单、状态机、串行任务、进程归属
runtime_paths.py            项目内运行路径与检查
tests/test_environment.py   并发、停止、超时及恢复测试
.runtime/                  锁、日志、状态、缓存，加入 gitignore
```

继续修改现有 app.py、weather_service.py、static/app.js、templates/index.html。

| 模块 | 用法 |
| --- | --- |
| Flask | 列表、任务、状态和错误接口 |
| subprocess | Popen 启动、退出码、等待与回收 |
| os / signal | 环境变量、进程组和有界停止 |
| threading / queue | 唯一后台执行者和串行任务 |
| fcntl | Linux 项目内进程间锁 |
| pathlib / json | 固定路径、原子状态持久化 |
| time / uuid | 单调超时和任务/实例标识 |
| msgpack / socket | 复用 RPC ping 和天气设置 |

第一版无需新增 AirSim SDK、Celery 或 Redis。进程锁和单例要求随部署方式落实。进程控制接口需限制操作者权限、校验修改请求来源并防止 CSRF；同源检查本身不等于身份认证，不能默认所有网络访问者都是管理员。

## 11. 实施与验收顺序

### 第一阶段：验证可行性

验证 X11 认证、常驻空 xpra、新窗口发现，以及三个旧包的只读资源访问与输出路径。先确认目录外写入约束满足；否则先处理隔离或受限运行方式，不修改外部应用迁就实现。

### 第二阶段：实现与联调

实现白名单、唯一管理器、异步任务、状态接口、天气门禁；前端替换为三个固定选项和真实任务状态。必要时先使用方案 A 验证生命周期，再切换为常驻方案，不能预先宣称画面始终不断连。

### 第三阶段：验收

1. 三个环境之间六种有向切换：旧进程退出、新窗口出现、RPC 就绪和默认晴朗。
2. 每个环境的雨、雾、雪、晴朗，以及切换后键盘和鼠标操作。
3. 重复点击、两浏览器同时切换、切换中天气请求、旧响应迟到、页面刷新。
4. 启动失败、进程崩溃、RPC 端口被外部占用、xpra 故障和管理服务重启。
5. 失败不误选成功，不残留两个受管模拟器，不误杀其他进程，不写入未获准目录。
6. 正常切换时同一个 iframe 能呈现新窗口；如需重连，界面明确展示恢复过程。

验收分别记录进程、RPC、画面和输入四层证据。单元测试、TCP 或 ping 成功均不能替代窗口与键鼠实测。完成这些验证后再考虑多实例、预热和开机自启。
