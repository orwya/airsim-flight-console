"""命令行入口与进程生命周期。"""

import argparse
import signal

from app import create_app
from config import build_settings


def build_parser():
    parser = argparse.ArgumentParser(description="AirSim Flight Console")
    parser.add_argument("--ip", default="127.0.0.1", help="后端监听与画面访问共用的 IP")
    parser.add_argument("--http-port", type=int, default=5173)
    parser.add_argument("--display", default=":100")
    parser.add_argument("--xpra-port", type=int, default=10000)
    parser.add_argument("--settings", default="/data/airsim/settings.json")
    parser.add_argument("--planner-port", type=int, help="本机 Planner HTTP 端口；省略则关闭")
    parser.add_argument("--planner-timeout", type=float, default=5.0)
    parser.add_argument("--airsim-timeout", type=float, default=5.0)
    for option, default in (
        ("port-release-wait", 10),
        ("kill-wait", 5),
        ("xpra-ready-timeout", 30),
        ("rpc-ready-timeout", 120),
        ("poll-interval", 0.2),
    ):
        parser.add_argument("--" + option, type=float, default=default)
    return parser


def main(argv=None):
    settings = build_settings(build_parser().parse_args(argv))
    app = create_app(settings)
    scenes = app.extensions["services"].scenes

    def terminate(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, terminate)
    try:
        scenes.submit(cleanup=True)  # 启动清扫上一轮残留会话
        app.run(
            host=settings.ip, port=settings.http_port, debug=False, use_reloader=False
        )
    finally:
        scenes.shutdown()


if __name__ == "__main__":
    main()
