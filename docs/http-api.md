# HTTP API

控制台的 HTTP 接口。入口是 `http://<服务器 IP>:5173`，下面所有路径都相对它。响应都是 JSON，`/api/*` 带 `Cache-Control: no-store`。

Planner 的接口不在本文范围内，见最后一节。

## 请求约定

- `POST` 请求用 `Content-Type: application/json`。
- 请求带 `Origin` 时，必须与控制台来源一致。
- `Sec-Fetch-Site: cross-site` 的请求直接拒绝，返回 `403` 和 `{"error": "cross_origin"}`。

接口没有登录环节。这条规则针对浏览器发起的跨站 POST。

场景切换和天气设置由服务端串行化：同一时刻只执行一个，其余请求返回 `409 environment_busy`。所有浏览器共用这份状态。

## 存活与配置

| 方法与路径 | 返回 |
| --- | --- |
| GET /api/health | `status: "ok"`、`backend: "flask"`。只说明 Flask 在跑 |
| GET /api/config | `console_url`、`xpra_url`、`display_mode`、`planner_url`、`weather_control_available` |
| GET /api/scenes | `scenes` 数组，每项含 `id`、`name`、`available` |
| GET /api/status | 场景生命周期快照 |
| GET /api/airsim | 对已就绪实例发一次 RPC ping。成功 `200` 与 `connected: true`；未就绪或不可达 `503` |

`planner_url` 由 `--ip` 和 `--planner-port` 拼出，只用于页面预填。它为空说明没配端口；Planner 是否可用看页面的连接结果。

场景 `id` 固定为 `airsimnh`、`abandonedpark`、`msbuild2018`，区分大小写。`available` 只反映程序文件存在且有执行权限，启动仍可能失败。

### 状态快照

字段：`phase`、`stage`、`error`、`scene`、`target_scene`、`weather`、`instance_id`、`manager_id`、`revision`、`operation_id`、`completed_operation_id`，以及端口和清理信息。

`phase` 取值：`idle`、`stopping`、`starting`、`initializing`、`ready`、`error`。

- `manager_id` 标识管理器进程。换了 `manager_id` 说明后端重启过，旧响应作废。
- `revision` 在同一管理器内递增，用来丢弃旧响应。
- `operation_id` 标识一次异步操作。完成后对照 `completed_operation_id`、`phase`、`error` 判断结果。
- `instance_id` 标识一次 AirSim 启动。改天气必须带当前实例的 ID。
- `ready` 表示切换流程走完并且端口、窗口都校验过。之后不再持续探测，飞行状态要看 RPC 和 Planner。

## 场景切换与清理

两个操作都会重置 AirSim。页面在提交前会问一次 Planner 状态，只有 `running` 时才弹确认。

### POST /api/switch

请求体只有一个字段：

    {"scene_id": "airsimnh"}

新切换返回 `202` 和状态快照，之后轮询 `GET /api/status`。目标场景已经是受管的就绪实例时返回 `200`，不重启。`202` 只表示操作被接受，启动失败会在之后的状态里报出来。

| 状态码 | error | 含义 |
| --- | --- | --- |
| 400 | invalid_scene | `scene_id` 无效 |
| 409 | environment_busy | 已有受保护操作在进行 |
| 503 | environment_error | 运行目录、归属检查或操作准备失败 |

### POST /api/cleanup

不带请求体，返回 `202` 和状态快照。异步停止并清理已确认归属的会话。

## 天气

    {"weather": "clear", "instance_id": "<取自 /api/status>"}

`weather` 支持 `clear`、`rain`、`fog`、`snow`。每次应用完整预设，会覆盖掉其他预设残留的参数。

成功返回 `200`：

    {"weather": "clear", "status": "applied", "instance_id": "<当前实例 ID>"}

| 状态码 | error | 含义 |
| --- | --- | --- |
| 400 | invalid_weather | 天气名称无效 |
| 409 | environment_busy | 操作冲突、场景未就绪，或 `instance_id` 已失效 |
| 503 | airsim_unavailable | 天气 RPC 失败或超时 |

新实例由后端初始化成晴朗。页面首次连接时也会应用一次晴朗，所以刷新页面可能改掉其他页面设的天气。

## Planner：页面直连，不经过本服务

Planner 的请求由浏览器直接发出。地址取页面的预填值，或者右上角“连接设置”里保存的值。

| 页面请求 | 说明 |
| --- | --- |
| GET `<planner>/api/status` | 返回 `connecting`、`idle`、`running`、`stopping` |
| GET `<planner>/api/logs?cursor=N` | 增量日志。下一次请求用返回的 `next_cursor` |
| POST `<planner>/api/tasks` | 请求体含一个 `instruction` 字段 |
| POST `<planner>/api/stop` | 不带请求体，停止当前全局任务 |

### 跨域

浏览器从控制台来源访问 Planner 属于跨源请求，需要 Planner 侧配合两件事：

- 响应带 `Access-Control-Allow-Origin`，值为控制台来源或 `*`。
- 响应 `OPTIONS` 预检，允许 `POST`、`GET` 和 `Content-Type`。`POST` 带 JSON 请求体时浏览器一定会先发预检。

页面用 `credentials: omit`，不发送 Cookie。控制台页面走 https 时无法访问 http 的 Planner。

### 错误与重试

字段校验、游标格式和 4xx/5xx 的语义都由 Planner 决定。地址不可达或跨域被拒时，浏览器只抛出 `TypeError`，页面统一显示“浏览器无法访问 <地址>”。

提交和停止都不自动重试。超时按结果未确认处理：请求可能已经被接受，页面保留输入并提示先查状态和日志。

### 切换场景前的检查

页面在切换或清理前请求一次 `/api/status`：

| 状态 | 行为 |
| --- | --- |
| `running` | 弹确认框：先停止再切换、直接切换、取消 |
| `connecting`、`stopping`、`idle` | 直接切换 |
| 请求失败 | 直接切换，连接异常只显示在状态栏 |

## 测试覆盖

自动测试覆盖路由、状态机、端口派生和场景操作的互斥。实机验收范围见 [README](../README.md)。
## 跨设备显示当前指令

浏览器每秒直接查询同一个 Planner 的 GET /api/status，读取 state 和 instruction：

    {"state": "running", "instruction": "上升10m"}

instruction 为用户提交的完整指令原文。running、stopping 时显示该内容；idle、connecting 时清空当前指令。新打开或刷新页面也从此接口恢复显示，无需同步其他浏览器的输入框或本地存储。

该功能要求 Planner 实现带 instruction 字段的状态接口。旧版本只返回 state 时，本页仍可显示自己提交的指令，其他设备只能显示内容未知。HTTP 状态中的 idle 不能证明任务成功。
