# HTTP API

本文对应当前 Flask 控制台。默认入口为 http://<服务器 IP>:5173；所有路径均相对此入口。接口返回 JSON，/api/* 响应设置 Cache-Control: no-store。

## 请求约定

POST 使用 Content-Type: application/json。带 Origin 时必须与控制台来源一致，Sec-Fetch-Site: cross-site 会被拒绝，返回 403 cross_origin。该检查不是登录鉴权。

所有页面共享一个场景和一个 Planner。提交任务、切换场景与设置天气使用操作门禁；门禁不覆盖任务整个执行周期，也不能阻止其他客户端直接访问 Planner。

## 存活与配置

| 方法与路径 | 返回与含义 |
| --- | --- |
| GET /api/health | status: ok、backend: flask，仅表示 Flask 存活 |
| GET /api/config | console_url、xpra_url、display_mode、planner_timeout 和功能标志 |
| GET /api/scenes | scenes 数组，每项包含 id、name、available |
| GET /api/status | 场景生命周期快照 |
| GET /api/airsim | 已就绪实例的实际 RPC ping；200 connected: true，未就绪或不可达时 503 |

flight_control_available 和 planner_control_available 只表示启动时是否配置 Planner 端口，不表示上游当前可达或无人机已经可控。

场景 ID 固定为 AirSimNH、AbandonedPark、MSBuild2018；available 来自程序存在性和执行权限检查，不代表启动必然成功。

状态快照包含 phase、stage、error、scene、target_scene、weather、instance_id、manager_id、revision、operation_id、completed_operation_id、端口与清理信息。客户端应以实际返回字段为准。phase 包括 idle、stopping、starting、initializing、ready、error。

- manager_id 区分管理器进程；同一管理器中的 revision 用于丢弃旧响应。
- operation_id 追踪异步操作；完成后结合 completed_operation_id、phase、error 判断结果。
- instance_id 区分每次启动的 AirSim。不能用旧实例 ID 修改新实例天气。
- ready 不是持续飞行健康检查，仍需结合 RPC 和 Planner 状态。

## 场景切换与清理

POST /api/switch 请求示例：

    {"scene_id": "AirSimNH"}

新切换返回 202 和状态快照，随后轮询 GET /api/status。已就绪的同一受管场景可返回 200。202 仅表示操作被接受，启动失败会在后续状态中报告。

POST /api/cleanup 不要求请求体，返回 202 和状态快照。它异步停止并清理已核验归属的会话；不会接管陌生进程。

| 状态码 | 含义 |
| --- | --- |
| 400 invalid_scene | 场景 ID 无效 |
| 409 environment_busy | 其他受保护操作正在进行 |
| 503 environment_error | 运行目录、归属检查或操作准备失败 |

配置 Planner 时，切换前必须确认停止和空闲；新场景就绪前还会等待 Planner 恢复空闲。检查失败不会被当作停止成功。

## 天气

POST /api/weather：

    {"weather": "clear", "instance_id": "<从 /api/status 取得的当前实例 ID>"}

weather 支持 clear、rain、fog、snow。每次应用完整预设，清除其他预设的残留参数。成功返回 200：

    {"weather": "clear", "status": "applied", "instance_id": "<当前实例 ID>"}

| 状态码 | 含义 |
| --- | --- |
| 400 invalid_weather | 天气名称无效 |
| 409 environment_busy | 操作冲突、场景未就绪或实例 ID 失效 |
| 503 airsim_unavailable | 天气 RPC 失败或超时 |

新实例由后端初始化晴朗。网页首次连接时也会应用晴朗，刷新页面可能重置其他页面所见的共享天气。

## Planner 代理

必须通过 --planner-port 启用。上游地址固定为 127.0.0.1:<planner-port>，不接收浏览器指定的任意目标地址。

| 控制台路径 | 上游路径 |
| --- | --- |
| POST /api/planner/tasks | POST /api/tasks |
| GET /api/planner/status | GET /api/status |
| GET /api/planner/logs?cursor=N | GET /api/logs?cursor=N |
| POST /api/planner/stop | POST /api/stop |

上游 JSON 对象及 HTTP 状态码透传；代理不伪造任务成功，不自动重试 POST。一次请求使用独立 HTTP 连接，响应大小上限为 2 MiB。

### 提交任务

POST /api/planner/tasks 只接受一个 instruction 字段，必须为非空字符串；首尾空白会被移除：

    {"instruction": "飞到右前方第一辆红色汽车附近"}

控制台在场景 ready 且 Planner idle 时提交一次。正常兼容响应为 HTTP 202，包含 state: running 和不透明 task_id。客户端不得依赖任务 ID 的数字格式，也不能把 202 当作到达目标的证明。

字段缺失、空字符串、错误类型或额外字段返回 422，detail 为数组。操作门禁或场景状态冲突可返回 409 environment_busy；Planner 正在运行或尚未就绪时会返回相应的忙碌或不可用响应。

### 状态与日志

GET /api/planner/status 返回上游状态。state 为 connecting、idle、running、stopping：

- connecting：Planner 正在连接或准备。
- idle：可以接收任务，不代表上一任务成功。
- running：可能仍在等待模型、解析或规划，也可能已在飞行。
- stopping：停止处理中，需继续等待。

GET /api/planner/logs?cursor=0 读取日志，后续使用上游返回的 next_cursor。cursor 必须为最多 19 位 ASCII 非负十进制整数，否则返回 422。日志字段由兼容 Planner 定义；当前页面根据增量游标追加显示，并处理游标回退。

日志含执行结果线索，但当前没有结构化任务结果或按 task_id 查询结果的接口。不能只凭日志轮询停止或 state 变为 idle 宣告成功。

### 停止

POST /api/planner/stop 不要求请求体，向上游发送无请求体的停止请求。场景异常时也允许调用，便于中止全局任务。

停止响应不等于无人机已停稳，应继续观察 Planner 状态和执行日志。关闭页面、清空日志、切回键盘焦点均不会发起停止。

### 代理错误

| 状态码与 error | 含义 |
| --- | --- |
| 503 planner_disabled | 没有配置 Planner 端口 |
| 502 planner_unavailable | 建连或通信失败 |
| 502 planner_protocol | 响应不是合法 JSON 对象或超过大小限制 |
| 504 planner_timeout | 上游请求超时 |

上游返回的 409、422、503 等错误保留其状态和 JSON 对象，detail 可以是字符串、对象或数组，客户端应兼容显示。

请求超时或网络中断时，Planner 仍可能已接受任务。网页保留输入并提示结果未确认，不自动重发。应先查询状态和日志，确认后再决定下一步。

## 验证边界

接口的模拟测试验证路由、门禁、透传和错误处理，不代表模型理解或真实飞行一定成功。真实飞行、焦点、天气及场景切换的验收范围见 [README](../README.md)。
