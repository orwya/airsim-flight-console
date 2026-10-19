# AEROLYTE 飞行控制台

后端使用 Python / Flask，前端为原生 HTML、CSS、JavaScript。无需 Node.js 或前端构建。UI、环境与天气列表、任务记录及 xpra 嵌入保持原有行为。

## 启动

需要 Python 3.10 或更新版本。在项目目录创建虚拟环境：

```sh
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Linux / macOS：

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

打开 http://127.0.0.1:5173/ 。启动前结束旧的 Node 静态服务，避免端口冲突。HTML 由 Flask 模板渲染，不再直接双击打开。

若虚拟环境没有 pip，但系统 Python 有 pip，可执行 `python -m pip --python .venv install -r requirements.txt`。

局域网联调（在服务器执行）：

```sh
.venv/bin/python app.py --host 0.0.0.0 --port 5173
```

浏览器访问 `http://服务器IP:5173/`。内置服务器用于开发；Linux 正式部署可另行安装 Gunicorn 后运行 `gunicorn --bind 127.0.0.1:5173 app:app`。

## 结构与接口

```text
app.py                 Flask 工厂、配置与 HTTP 路由
requirements.txt       Python 依赖
static/app.js          UI 状态、xpra 连接逻辑
static/styles.css      样式
 templates/index.html  Jinja 页面模板
 tests/test_app.py     HTTP 与配置检查
```

| 路径 | 方法 | 用途 |
| --- | --- | --- |
| `/` | GET | 控制台页面 |
| `/static/app.js`、`/static/styles.css` | GET | 前端资源 |
| `/api/health` | GET | Flask 健康状态，不代表远程会话已连接 |
| `/api/config` | GET | 默认 xpra 地址、显示模式、控制能力状态 |

默认 xpra 地址为 `http://172.27.137.174:10000/`。通过环境变量 `XPRA_URL` 覆盖，重启 Flask 后生效：

```powershell
$env:XPRA_URL = 'http://172.27.137.174:10000/'
.\.venv\Scripts\python.exe app.py
```

```sh
XPRA_URL=http://172.27.137.174:10000/ .venv/bin/python app.py
```

浏览器连接设置中的已保存地址优先于 Flask 默认地址。仅接受 HTTP/HTTPS 网页地址，拒绝包含用户名、密码或 password/token 查询参数的服务器配置。

## xpra 与功能边界

浏览器 iframe 直接连接 xpra HTML5 客户端。Flask 不代理视频或 WebSocket，不修改远端响应头。当前电脑必须能访问 xpra；HTTP 200 和 iframe 加载事件不代表远程会话连接成功。

网页连接使用 sharing=true 与 steal=true。xpra 的 steal=false 会在共享判断前拒绝已有客户端时的新连接，不能用来实现多客户端访问。必须先在服务器开启 sharing=yes，才能确保多个客户端共享而不是相互替换。“返回演示”释放网页远程连接。

运行中的会话可在服务器执行：

```sh
xpra control :100 set-sharing yes
```

之后启动时始终添加 `--sharing=yes`。若命令不可用，请提供 `xpra --version` 和 `xpra control :100 help` 的输出，不要反复启动同一个显示号。独立浏览器也应使用 `http://172.27.137.174:10000/?sharing=true&steal=true`。

若拒绝嵌入，检查服务器 Content-Security-Policy 的 frame-ancestors 是否允许控制台实际来源。按当前需求允许任意网络来源可设为 `frame-ancestors *`，其余指令保持原样。HTTPS 控制台需配套 HTTPS/WSS xpra。配置说明：https://github.com/Xpra-org/xpra-html5/blob/master/docs/Configuration.md

显示的是 AirSim 应用窗口。无人机相机 API、自然语言飞行控制、环境与天气控制仍待接入；指令仅在浏览器会话中记录，不执行飞行。列表增减为会话 UI 状态，没有新增伪造执行成功的控制接口。

相机按钮仅在演示模式记录画面信息，远程模式不提供真实截图。演示图片来自 Unsplash，字体来自 Google Fonts，需联网，字体有本地回退。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Linux 使用 `.venv/bin/python`。
