"""All configuration lives here: built from the command line, validated once, URLs derived."""

from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import json
import math
from pathlib import Path
import re

DEFAULT_RPC_PORT = 41451  # AirSim 自身的默认端口
LOOPBACK = "127.0.0.1"  # AirSim RPC 只监听本机回环，与部署地址无关

TIMEOUT_FIELDS = (
    "airsim_timeout",
    "port_release_wait",
    "kill_wait",
    "xpra_ready_timeout",
    "rpc_ready_timeout",
    "poll_interval",
)


def airsim_rpc_port(settings_path):
    """AirSim RPC 端口：以 settings.json 的 ApiServerPort 为准，缺省用 AirSim 默认值。"""
    path = Path(settings_path)
    if not path.is_file():
        # 文件缺失时 AirSim 退回默认配置，端口同样是默认值。
        return DEFAULT_RPC_PORT
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("settings 必须是 JSON 对象")
    port = data.get("ApiServerPort", DEFAULT_RPC_PORT)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError(f"settings 的 ApiServerPort 无效：{port!r}")
    return port


@dataclass(frozen=True)
class Settings:
    ip: str = LOOPBACK  # 唯一地址：后端监听、浏览器访问、xpra 画面共用
    http_port: int = 5173
    display: str = ":100"
    xpra_port: int = 10000
    airsim_settings: str = "/data/airsim/settings.json"
    planner_port: int | None = None
    airsim_timeout: float = 5.0
    port_release_wait: float = 10.0
    kill_wait: float = 5.0
    xpra_ready_timeout: float = 30.0
    rpc_ready_timeout: float = 120.0
    poll_interval: float = 0.2
    rpc_port: int | None = None  # 留空则从 settings 文件解析，不是命令行参数
    root: Path = field(default_factory=lambda: Path(__file__).resolve().parent)

    @property
    def console_url(self):
        return f"http://{self.ip}:{self.http_port}/"

    @property
    def xpra_url(self):
        return f"http://{self.ip}:{self.xpra_port}/"

    @property
    def planner_url(self):
        """浏览器直连 Planner 的地址。未配置端口时为空，页面可手动填写。"""
        return f"http://{self.ip}:{self.planner_port}" if self.planner_port else ""

    def __post_init__(self):
        if self.rpc_port is None:
            object.__setattr__(self, "rpc_port", airsim_rpc_port(self.airsim_settings))
        try:
            address = ipaddress.ip_address(self.ip)
        except ValueError as error:
            raise ValueError(f"--ip 必须是具体的 IP 地址：{self.ip}") from error
        if address.is_unspecified:
            raise ValueError("--ip 不能是 0.0.0.0 或 ::，否则浏览器无法访问")
        if not all(
            type(p) is int and 1 <= p <= 65535
            for p in (self.http_port, self.xpra_port, self.rpc_port)
        ):
            raise ValueError("端口必须在 1-65535 之间")
        if len({self.http_port, self.xpra_port, self.rpc_port}) != 3:
            raise ValueError("后端端口、画面端口、RPC 端口必须互不相同")
        if self.planner_port is not None:
            if type(self.planner_port) is not int or not 1 <= self.planner_port <= 65535:
                raise ValueError("--planner-port 必须在 1-65535 之间")
            if self.planner_port in (self.http_port, self.xpra_port, self.rpc_port):
                raise ValueError("Planner 端口不能与控制台、画面、RPC 端口相同")
        if not re.fullmatch(r":[1-9][0-9]{0,4}", self.display):
            raise ValueError("--display 必须形如 :100")
        for name in TIMEOUT_FIELDS:
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 < value <= 600:
                raise ValueError(f"--{name.replace('_', '-')} 必须在 (0, 600] 之间")
        if not Path(self.root).is_dir():
            raise ValueError(f"项目根目录不存在：{self.root}")
        object.__setattr__(self, "root", Path(self.root).resolve())


def build_settings(args):
    """命令行参数 → Settings。读取 settings 文件只在这里发生一次。"""
    return Settings(
        ip=args.ip,
        http_port=args.http_port,
        display=args.display,
        xpra_port=args.xpra_port,
        airsim_settings=args.settings,
        airsim_timeout=args.airsim_timeout,
        planner_port=args.planner_port,
        port_release_wait=args.port_release_wait,
        kill_wait=args.kill_wait,
        xpra_ready_timeout=args.xpra_ready_timeout,
        rpc_ready_timeout=args.rpc_ready_timeout,
        poll_interval=args.poll_interval,
    )
