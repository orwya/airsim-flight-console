# AirSim 网页控制台完整实现说明

> 历史资料：本文保留早期设计和实现记录，部分内容已被后续代码替代，不能直接作为当前部署步骤。当前版本未使用 bwrap 或 HOME/XDG 重定向；场景切换采用完整重启 xpra/AirSim，并已增加 Planner 代理与生命周期协调。请以 [README](../README.md) 和 [HTTP API](http-api.md) 为准。


更新日期：2026-10-22
部署目录：/data/airsim-flight-console
服务器：172.27.137.174
对应代码版本：521c755b5e71d277c0adcb76c4dc2bc375b515df

本文依据当前代码说明已经实现的行为，包括应用窗口接入、天气控制、环境切换、进程清理和前端状态同步。原设计文档 airsim-environment-switch-plan.md 保留用于参考；本文以实际实现为准。

## 1. 功能范围与总体架构

当前支持三个固定环境：AirSimNH、AbandonedPark、MSBuild2018。浏览器可启动环境、切换环境、停止并清理受管会话，并设置晴朗、雨天、雾天、雪天。

环境切换采用完整重启：停止旧 xpra 及其 AirSim，等待端口释放，再启动目标环境。不是同一 Unreal 进程内的地图热切换，也不是保留 xpra 仅更换子应用。切换会重置原实例的飞行位置、控制状态及未保存仿真状态。

```text
浏览器
 ├─ HTTP :5173 → Flask API → EnvironmentService
 │                            ├─ 进程归属检查、bwrap、xpra 启停
 │                            └─ MessagePack RPC :41451 → AirSim 天气
 └─ iframe / WebSocket :10000 → xpra :100 → AirSim 应用窗口

外部 planner ── MessagePack RPC :41451 ── AirSim
```

画面通道和控制通道分离：xpra 负责窗口画面及键鼠转发；Flask 调用 RPC 修改天气和管理生命周期。网页没有把 AirSim 视频逐帧读入 Python。

所有客户端共享同一个环境和天气。当前自然语言飞行任务只记录在浏览器会话，尚未调用无人机控制接口；真实截图、独立无人机相机视频流也尚未实现。

## 2. 代码分工

| 文件 | 主要职责 |
| --- | --- |
| app.py | Flask 路由、配置、命令行入口、跨来源写入检查、退出清理 |
| scene_launcher.py | 固定环境白名单、可执行文件路径、xpra 启停参数 |
| scene_processes.py | 监听进程归属、文件系统隔离、受管进程树清理、窗口 map/focus |
| environment_service.py | 异步切换任务、状态机、互斥门禁、实例标识、天气协同 |
| weather_service.py | MessagePack RPC 连接、天气预设、超时和异常处理 |
| templates/index.html | 控制台 HTML 模板、iframe、场景遮罩与功能入口 |
| static/app.js | 画面连接、天气交互、焦点恢复、通用界面逻辑 |
| static/scenes.js | 场景请求、状态轮询、遮罩与实例同步 |
| tests/ | Python 后端测试与 Node.js 前端逻辑测试 |

页面标题为“AirSim 飞行控制台”，已移除 AEROLYTE 品牌宣传、原首页宣传文案及左下角帮助/设置入口。浏览器存储仍使用历史内部键 aerolyte-xpra-url 保存画面地址，它不是界面展示文本。

## 3. 获取 AirSim 应用窗口

### 3.1 xpra 提供远程窗口

AirSim 在服务器 X11 显示 :100 中运行。xpra 开启 HTML 客户端和 TCP 10000 端口，浏览器通过 iframe 加载该地址，HTML 客户端再建立画面和输入连接。

默认地址：http://172.27.137.174:10000/ 。Flask 网页地址为 http://172.27.137.174:5173/ 。

前端连接时设置 floating_menu=false、border=no、clipboard=false、printing=false、file_transfer=false、sound=false、keyboard=false、steal=true、sharing=true。其中 keyboard=false 关闭的是 xpra 虚拟屏幕键盘，不是禁止物理键盘控制。

iframe 的 load 事件只能说明页面加载事件发生，不能证明远程窗口已可见、WebSocket 已成功连接或键鼠已通过实机验收。

### 3.2 天气切换后恢复输入焦点

点击网页天气按钮后，浏览器焦点会离开 AirSim iframe。static/app.js 在手动天气请求结束后，对仍然有效的当前实例调用 iframe.focus() 及 contentWindow.focus()，并提供“恢复控制”入口。

恢复焦点会检查场景状态、当前输入位置和弹窗等条件，避免在用户输入文本时无故抢占焦点。旧实例的迟到响应不会给新实例恢复焦点。

跨来源 iframe 不读取内部 DOM。鼠标视角捕获可能仍需用户点击一次画面；浏览器对指针锁定的限制不能靠后台天气请求绕过。焦点恢复也不等于调用 enableApiControl 或重新解锁无人机。

## 4. 固定环境和启动命令

### 4.1 白名单

scene_launcher.py 使用只读场景映射，只接受以下 scene_id：

| scene_id | 环境名称 | 实际二进制 |
| --- | --- | --- |
| airsimnh | AirSimNH | /data/airsim/AirSimNH/LinuxNoEditor/AirSimNH/Binaries/Linux/AirSimNH |
| abandonedpark | AbandonedPark | /data/airsim/AbandonedPark/LinuxNoEditor/AbandonedPark/Binaries/Linux/AbandonedPark |
| msbuild2018 | MSBuild2018 | /data/airsim/MSBuild2018/LinuxNoEditor/MSBuild2018/Binaries/Linux/MSBuild2018 |

浏览器只能提交 scene_id，不能提交启动命令、可执行路径或额外参数。GET /api/scenes 根据文件存在和执行权限返回 available。

原安装目录的 .sh 包含 chmod 操作。实现直接执行已经具备权限的二进制，避免改动 /data/airsim 下的脚本、权限和配置。

### 4.2 命令构造

get_commands() 返回参数数组，外层 subprocess 以 shell=False 执行。xpra 的 --start-child 字符串通过 shlex.join() 编码固定子程序参数。

主要启动参数如下，具体参数以 scene_launcher.py 为准：

```text
/usr/bin/xpra start :100
  --backend=x11 --daemon=no --use-display=no --start-via-proxy=no
  --start-child=<固定 AirSim 二进制及参数>
  --exit-with-children=yes --terminate-children=yes --sharing=yes
  --bind-tcp=0.0.0.0:10000 --html=on
  --dbus=no --dbus-launch= --pulseaudio=no
  --notifications=no --mdns=no --printing=no --file-transfer=no
  --socket-dir=<项目>/.runtime/sockets
  --socket-dirs=<项目>/.runtime/sockets
  --sessions-dir=<项目>/.runtime/sessions
  --log-dir=<项目>/.runtime/logs
  --pidfile=<项目>/.runtime/xpra.pid
```

Xvfb 创建 1280×720、24 位色的显示，并开启所需 GLX、RANDR、RENDER、Composite 扩展。AirSim 以窗口模式运行，参数包括：

```text
<二进制> <环境名称>
  -settings=/data/airsim-flight-console/.runtime/settings.json
  -userdir=/data/airsim-flight-console/.runtime/home
  -abslog=/data/airsim-flight-console/.runtime/logs/<scene_id>.log
  -windowed -ResX=1280 -ResY=720
```

停止和窗口控制使用明确会话地址 socket:///data/airsim-flight-console/.runtime/sessions/100/socket，避免仅凭 :100 全局查找而控制到其他会话。

## 5. 文件写入边界和 settings

### 5.1 运行目录

所有持久运行输出集中在项目 .runtime 内，目录已被 Git 忽略：

```text
/data/airsim-flight-console/.runtime/
  home/ cache/ config/ data/ run/ tmp/
  sockets/ sessions/
  settings.json
  manager.lock
  xpra.pid
  logs/launcher.log
  logs/airsimnh.log
  logs/abandonedpark.log
  logs/msbuild2018.log
```

启动环境中的 HOME、TMPDIR 和 XDG 缓存、配置、数据、运行路径均指向项目内；移除继承的 LD_LIBRARY_PATH、PYTHONPATH、XAUTHORITY、DBUS_SESSION_BUS_ADDRESS、SESSION_MANAGER、XPRA_SESSION_DIR，减少外部会话污染。

### 5.2 bwrap 文件系统隔离

ProcessRuntime.sandbox() 为 xpra 启动、停止及控制命令添加以下包装：

```text
/usr/bin/bwrap
  --ro-bind / /
  --bind /data/airsim-flight-console /data/airsim-flight-console
  --tmpfs /tmp
  --dev-bind /dev /dev
  --tmpfs /dev/shm
  --proc /proc
  --chdir /data/airsim-flight-console
  -- <实际参数数组>
```

外部普通文件系统只读，项目目录可写；X11 临时目录和共享内存位于私有临时挂载中。启动前先用 bwrap 执行 /usr/bin/true 检查能力，失败即拒绝启动，不静默降级。

这是文件系统写入边界，不是完整的网络或设备安全隔离。GPU 设备和网络仍保留给 AirSim、xpra 和 RPC 使用。

### 5.3 配置副本

后端只读 /data/airsim/settings.json，要求内容为 JSON 对象。保留源配置字段，并在副本中设置 ApiServerPort 为当前 RPC 端口、RpcEnabled=true。

副本通过临时文件加原子替换写入 .runtime/settings.json，不修改源文件。目标程序、源配置和 bwrap 的预检查先于停止旧环境，减少因配置错误造成不必要的旧实例中断。

## 6. 异步场景切换与状态机

### 6.1 状态含义

| phase | 含义 |
| --- | --- |
| idle | 没有就绪的受管场景 |
| stopping | 正在检查归属、停止旧会话和等待端口释放 |
| starting | 已启动目标会话，等待端口、RPC 和窗口 |
| initializing | 正在向新实例写入完整晴朗预设 |
| ready | 目标实例初始化完成，可以提交天气 |
| error | 启停或初始化失败，需要查看 error/stage |
| disabled | 当前应用入口未启用场景管理 |

正常切换：ready/idle → stopping → starting → initializing → ready。停止并清理：stopping → idle。任一阶段失败进入 error。

### 6.2 一次切换的完整流程

1. 校验 scene_id，非阻塞获取操作门禁；繁忙则返回 409。
2. 准备运行目录、获取 flock 唯一管理器锁、检查隔离能力。
3. 如果目标就是当前已就绪场景，重新检查端口归属，符合则返回 200，不重启。
4. 生成 operation_id，记录目标和上一场景，启动后台线程，接口返回 202。
5. 预检查目标二进制、settings 和隔离工具。
6. 检查 TCP/RPC 监听进程归属，正常停止旧 xpra；超时后只清理再次核验通过的进程树。
7. 确认两个端口均释放，清空旧实例标识，生成 settings 副本和新 instance_id。
8. 通过 bwrap 启动目标 xpra/AirSim，输出重定向到项目日志。
9. 等待画面端口、RPC、目标归属和对应窗口满足要求。
10. 应用完整 clear 预设，成功后公布 ready、实例和天气。
11. 无论成功或失败，最终释放操作门禁；失败记录错误，不无限重试或自动恢复旧飞行状态。

线程避免 HTTP 请求一直等待 Unreal 启动。HTTP 202 仅代表任务已接受，必须继续查询 /api/status。

### 6.3 锁和标识

- gate：串行化场景切换、清理与天气写入，防止旧场景正在退出时还设置天气。
- state_lock：保护状态快照和更新。
- weather.lock：天气服务内部避免两个完整预设交错写入。
- manager.lock：Linux flock，阻止多个管理器同时操作本项目会话。
- manager_id：后端管理器实例标识；后端重启后改变。
- revision：同一管理器内状态更新序号，前端丢弃较旧响应。
- operation_id：一次切换或清理任务标识。
- instance_id：一次 AirSim 启动标识，避免端口复用后旧请求作用到新场景。
- completed_operation_id：最近完成任务标识；cleanup 保存已确认的停止对象和实际强杀记录。

状态保存在进程内，不作为持久任务队列。存在临时 Popen 身份记录和 xpra 自身 PID 文件，但清理不会仅凭旧 PID 文件决定强杀。

## 7. 安全停止、崩溃和重启恢复

scene_processes.py 使用 ss 查询 10000 和 41451 的监听 PID，再读取 /proc 的用户、实际可执行文件、参数和进程启动时间。

识别 AirSim 要求二进制与固定环境一致，并包含项目 settings 的精确参数。识别 xpra 要求解释器、xpra 路径、start、显示号、项目 socket 参数和已知子应用都符合；同时兼容 xpra setproctitle 将参数合并为一个字符串的形式。

任何监听归属未知、参数不匹配或 PID 无法确认，都会拒绝操作并报告错误。不会因为命令中出现 AirSim、:100 或端口号就认定归属，也不使用全局 pkill。

正常停止先对项目明确 socket 执行 xpra stop，最多等待 10 秒。未释放则重新检查归属，通过 /proc 各线程 children 遍历对应进程树，自下而上逐个重新核验身份后发送 SIGKILL，最多再等 5 秒。身份比较包含启动时间，减少 PID 被复用后的误操作风险。

后端启动时提交一次受管残留清理；正常退出设置取消事件，等待工作线程并执行有界清理。后端被 SIGKILL 后可能留下应用，但下次启动可重新识别和清理本项目残留。

原先手动启动、使用外部 settings 或其他 socket 的 xpra/AirSim 不自动接管。“停止并清理”同样遵守归属检查。若外部进程占端口，应由操作者停止对应外部会话。

## 8. 就绪检测：为什么需要 map/focus

端口开放不代表游戏线程和窗口已经可用。启动阶段依次检查：

1. xpra TCP 端口可连接，启动进程没有提前退出。
2. RPC 端口可连接，其监听归属与目标环境一致，ping 返回 true。
3. xpra info 中存在目标 PID 对应的窗口。
4. 对该窗口执行 xpra control map 和 focus，均成功。
5. 完整晴朗天气调用成功后才进入 ready。

实际调试发现，没有浏览器客户端时，xpra seamless 窗口可能没有映射；此时 AirSim ping 可以成功，simEnableWeather 却因 Vulkan/GameThread 等待而超时。先 map/focus 目标窗口解决了这条初始化路径的问题。

普通 /api/status 只检查生命周期状态和实时端口，不会每次轮询都完整验证窗口、渲染或全部 RPC。因此 ready 也不能替代浏览器画面和键鼠的人工验收。

## 9. 天气 RPC 及 Python 库使用

### 9.1 依赖和职责

requirements.txt 只有两个第三方 Python 依赖：Flask>=3.1,<4.0 和 msgpack>=1.0,<2.0。当前天气实现没有依赖官方 airsim Python 包，也没有引入其旧版 Tornado/NumPy 依赖链。

| 库/模块 | 本项目中的用法 |
| --- | --- |
| Flask | 路由、JSON 请求/响应、模板渲染、配置和错误处理 |
| msgpack | packb 编码请求，Unpacker 增量解析 TCP 分片响应 |
| socket | 连接本机 AirSim RPC、端口探测、收发字节与超时 |
| threading | 后台任务、互斥锁、退出取消事件 |
| subprocess | 执行固定 argv、检查 xpra/ss、管理启动子进程 |
| pathlib、os | 路径、文件权限/环境、/proc 读取、settings 原子替换 |
| fcntl | Linux flock 唯一管理器锁 |
| signal | 处理退出信号、对核验过的进程发送终止信号 |
| time | monotonic 超时截止时间和短间隔状态检查 |
| contextlib | contextmanager 封装一条有生命周期的 RPC 连接 |
| json、uuid | 配置读写和管理器/任务/实例标识 |
| dataclasses、types | Scene/SceneConfig 配置对象和只读场景映射 |
| shlex | 安全编码 xpra start-child 参数、解析进程标题 |
| re | 显示号校验及 ss/xpra 输出解析 |
| argparse、urllib.parse | 命令行配置和 URL 校验 |

这些库围绕本机 Linux 进程管理使用；不能直接把该管理器原样部署到 Windows。

### 9.2 MessagePack RPC 协议

AirSimWeatherService 每次探测或预设操作建立一条 TCP 连接。一个完整天气操作中的调用共享连接和总超时预算。

```text
请求：[0, request_id, method, args]
响应：[1, request_id, error, result]

ping()
simEnableWeather(true)
simSetWeatherParameter(parameter_id, float_value) × 8
```

请求用 msgpack.packb(..., use_bin_type=True) 编码。响应由 msgpack.Unpacker(raw=False, max_buffer_size=1MB) 逐块接收，校验类型、长度、响应序号和服务端错误。处理 TCP 分片、断连和超时，而不是假设一次 recv 就收到完整对象。

使用 time.monotonic() 计算截止时间，后续每次调用只使用剩余预算。普通天气默认总超时 5 秒，新场景初始化使用独立的 30 秒天气服务。错误带上当前 RPC 方法，便于区分 connect、ping 和天气调用失败。

### 9.3 四组完整预设

| 参数 | ID | 晴朗 clear | 雨天 rain | 雾天 fog | 雪天 snow |
| --- | --- | --- | --- | --- | --- |
| rain | 0 | 0 | 0.84 | 0 | 0 |
| roadwetness | 1 | 0 | 0.95 | 0.05 | 0 |
| snow | 2 | 0 | 0 | 0 | 0.58 |
| road_snow | 3 | 0 | 0 | 0 | 0.72 |
| maple_leaf | 4 | 0 | 0 | 0 | 0 |
| road_leaf | 5 | 0 | 0 | 0 | 0 |
| dust | 6 | 0 | 0 | 0 | 0 |
| fog | 7 | 0 | 0.12 | 0.36 | 0.08 |

每次写入全部八个参数，包括清零值，防止雨切雪后遗留旧效果。调用不是原子事务，中途失败可能部分生效，此时状态标记为未确认，应重试完整预设。地面积水、积雪等视觉效果还取决于地图材质支持。

### 9.4 默认晴朗与实例校验

每个新实例由后端先初始化 clear。已经打开的网页切到新实例后同步该结果，不重复初始化；新打开或刷新网页仍保留首次连接自动应用晴朗的行为，所以其他页面刷新可能改变共享天气。后续定时轮询不会把手动天气不断重置为晴朗。

托管模式下天气请求必须携带当前 instance_id，且 phase=ready。过期实例、切换中或其他写操作占用时返回 409。请求成功后才更新选中状态；失败不会显示虚假的“已应用”。

## 10. 前端状态同步

static/scenes.js 每次只保留一个轮询请求，切换中约每秒一次、稳定时约每 5 秒一次；隐藏页面暂停轮询，恢复可见时继续。状态和切换请求设置 10 秒前端超时，天气请求为 35 秒。

切换开始立即显示遮罩、禁用环境/天气写入并清空旧实例显示。后台状态尚未 ready 时，不允许旧画面继续作为可操作的新环境呈现。

收到新 ready 的 instance_id 后，即使 xpra URL 没变，也重新连接 iframe，让客户端连接到新会话。场景请求结束不会直接解除忙碌状态，而是由后续真实状态决定。

为避免迟到响应覆盖当前状态：

- manager_id 改变时重置 revision 比较；同一管理器内拒绝较小 revision。
- 切换提交期间忽略旧的在途状态响应；接受任务后记住新 revision。
- 天气响应必须同时匹配发出时和当前 instance_id。
- 天气请求进行中，不用轮询结果覆盖当前请求的显示。
- 状态获取失败时显示错误遮罩并禁用天气；不能假定旧 ready 仍然有效。
- 后端天气值未知时显示“未确认”，不伪造晴朗或成功状态。

## 11. HTTP 接口与调用方法

| 方法与路径 | 内容 |
| --- | --- |
| GET /api/health | Flask 健康，不代表 AirSim 就绪 |
| GET /api/config | xpra 地址、画面模式和能力开关 |
| GET /api/scenes | 固定环境列表、available 和 enabled |
| GET /api/status | 生命周期、实例、天气、端口、阶段、错误及清理记录 |
| POST /api/switch | JSON scene_id；新任务 202，同一就绪场景 200 |
| POST /api/cleanup | 异步停止并清理，202 |
| GET /api/airsim | 就绪场景实际 RPC ping |
| POST /api/weather | JSON weather、instance_id；成功返回 applied 和实例标识 |

错误约定：无效参数 400；跨来源写入 403；繁忙、未就绪或过期实例 409；管理未启用、归属/环境不可用或 RPC 失败 503。异步任务接受后的失败通过 /api/status 的 error 返回，不能只看启动请求的 202。

以下为人工调用示例；switch、weather、cleanup 会改变全局模拟器状态：

```bash
curl -s http://127.0.0.1:5173/api/scenes
curl -s http://127.0.0.1:5173/api/status

curl -s -X POST http://127.0.0.1:5173/api/switch \
  -H 'Content-Type: application/json' \
  -d '{"scene_id":"abandonedpark"}'

# 轮询 status 等待 phase=ready，再复制其 instance_id：
curl -s -X POST http://127.0.0.1:5173/api/weather \
  -H 'Content-Type: application/json' \
  -d '{"weather":"rain","instance_id":"替换为当前实例ID"}'

curl -s -X POST http://127.0.0.1:5173/api/cleanup
```

所有 API 响应设置 Cache-Control: no-store。POST 检查 Origin 和 Sec-Fetch-Site 拒绝跨来源浏览器写入；这不等于登录鉴权，当前系统适用于受控可信网络。

## 12. 部署和参数

服务器需要 Linux、Python 3.10+、xpra、Xvfb、ss、bwrap 和已有 GPU 驱动。当前 xpra 6.5.5 支持实现使用的 map/focus 命令。

已有环境不需要重复创建虚拟环境或安装依赖。首次部署参考：

```bash
cd /data/airsim-flight-console
python3 -m venv .venv
TMPDIR="$PWD/.venv" PIP_CACHE_DIR="$PWD/.venv/pip-cache" \
  .venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py --host 0.0.0.0 --port 5173
```

python app.py 自动启用场景管理并先提交残留清理；不需要预先手动启动 xpra。导入 app:app 默认不启用场景管理，避免普通模块导入产生进程控制副作用；显式配置 SCENE_CONTROL_ENABLED=1 才创建管理器。

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| --host / --port | 127.0.0.1 / 5173 | Flask 监听 |
| --display | :100 | xpra 显示号 |
| --tcp-port | 10000 | xpra 端口 |
| --rpc-port | 41451 | AirSim 与天气统一 RPC 端口 |
| --settings | /data/airsim/settings.json | 只读源配置 |
| --port-release-wait | 10 秒 | 正常停止后释放端口上限 |
| --kill-wait | 5 秒 | 核验强杀后释放端口上限 |
| --tcp-ready-timeout | 30 秒 | xpra 画面端口就绪上限 |
| --rpc-ready-timeout | 120 秒 | RPC、归属与窗口就绪上限 |
| --poll-interval | 0.2 秒 | 后端检查间隔 |
| XPRA_URL | http://172.27.137.174:10000/ | 浏览器画面地址 |
| AIRSIM_TIMEOUT | 5 秒 | 普通天气操作总超时 |

修改 tcp-port 时需要同步 XPRA_URL。浏览器保存的画面地址优先于默认地址；HTTPS 网页需要相应 HTTPS/WSS 画面服务。

必须单进程运行，关闭 Flask reloader，不能使用多个 WSGI worker 各自持有状态。当前直接 CLI 入口包含启动清理、SIGTERM 和 finally 退出清理；其他部署入口需要自行接好生命周期，不能只设置环境变量就认为行为完全等价。

当前服务器日志位于 .venv/console.log，PID 记录在 .venv/console.pid，没有配置开机启动。重启前需等待旧管理器退出并释放锁。

## 13. 外部 planner 接入

planner 直接访问 41451，环境切换必然使原 RPC 连接失效。建议监测 /api/status：

1. 非 ready 时暂停发送指令。
2. 连接中断或 instance_id 改变后丢弃旧客户端及旧实例状态。
3. 等待新实例 ready，再重建 RPC 客户端，重新获取必要状态。
4. 按业务重新设置 API 控制、解锁和任务，不复用旧实例飞行假设。

Flask 的 gate 只能保护经过 Flask 的写操作，无法拦截 planner 直接发往 AirSim 的 RPC。多控制端还需约定指令所有权。

## 14. 验证结果与局限

已有验证记录截至 2026-10-22；本文档整理没有重新切换或重启运行中的环境。

- 后端 27 项 Python 测试通过，前端 12 项 Node.js 测试通过。
- 三环境之间六种有向切换均实机验证，最近一轮每次约 6–8 秒，包含该轮四组天气调用；不是启动时间保证或超时配置。
- 验证了繁忙时拒绝天气/第二次切换、拒绝旧实例、完整预设调用和新实例晴朗。
- 验证了外部 10000/41451 监听不被误杀、应用崩溃恢复、后端被强杀后重启清理、手动清理。
- 检查实际 AirSim 挂载命名空间，外部 /data 只读而项目目录可写。
- 窗口 PID、map/focus、RPC 和 HTTP 画面入口已分别检查；浏览器自动化读取/截图持续超时，真实画面与键鼠仍待人工验收。

主要证据为本机忽略目录中的 .runtime/integration-scenes.json、.runtime/integration-faults.json 及相关日志；它们是运行验证产物，不随代码仓库发布。

复现单元测试的命令如下；Node.js 18+，前端测试无需 npm 依赖：

```bash
cd /data/airsim-flight-console
.venv/bin/python -m unittest discover -s tests -v
node --test tests/test_frontend.cjs
node --check static/app.js
node --check static/scenes.js
```

## 15. 排查入口

| 现象 | 优先检查 |
| --- | --- |
| 切换返回 409 | /api/status 是否仍在 stopping/starting/initializing，或天气请求未结束 |
| 端口占用但拒绝清理 | error 中的 PID/参数是否属于手动或其他项目会话 |
| ping 成功但初始化超时 | launcher/Unreal 日志、目标窗口 PID、map/focus 是否成功 |
| 天气部分改变后报错 | RPC 错误方法和日志；ready 后重试完整预设 |
| 天气按钮后键盘无响应 | 先点“恢复控制”或实际画面，确认场景 ready 和实例未变化 |
| 新实例启动但网页画面旧 | instance_id 是否变化、iframe 是否重连、保存的 xpra 地址是否正确 |
| 后端报管理器锁占用 | 是否有旧后端仍运行；不要启动第二个管理器 |
| 隔离检查失败 | bwrap 和系统命名空间能力；不要移除隔离直接运行 |
| 页面显示 ready 但实际画面异常 | 检查浏览器连接及模拟器日志；端口检测不能代替渲染验收 |

后续扩展若需要多用户鉴权、多进程部署、持久任务或自动恢复，应先把生命周期管理拆成单独服务，再让网页通过明确接口访问，继续保留白名单、归属检查、实例校验和文件写入边界。
