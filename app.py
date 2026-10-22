"""Flask console and asynchronous, locally managed AirSim scene switching."""
import argparse
import os
from pathlib import Path
import signal
from urllib.parse import parse_qsl, urlsplit

from flask import Flask, jsonify, render_template, request

from environment_service import EnvironmentService, SceneBusy, SceneConfig
from scene_launcher import scene_list
from scene_processes import SceneError
from weather_service import AirSimWeatherService, WEATHER_PRESETS, WeatherBusy, WeatherUnavailable

def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        XPRA_URL=os.environ.get("XPRA_URL", "http://172.27.137.174:10000/"),
        AIRSIM_HOST=os.environ.get("AIRSIM_HOST", "127.0.0.1"),
        AIRSIM_PORT=int(os.environ.get("AIRSIM_PORT", "41451")),
        AIRSIM_TIMEOUT=float(os.environ.get("AIRSIM_TIMEOUT", "5")),
        SCENE_CONTROL_ENABLED=os.environ.get("SCENE_CONTROL_ENABLED", "0") == "1",
        SCENE_CONFIG=None,
    )
    if test_config:
        app.config.update(test_config)
    if not 1 <= app.config["AIRSIM_PORT"] <= 65535 or not 0 < app.config["AIRSIM_TIMEOUT"] <= 30:
        raise ValueError("Invalid AIRSIM_PORT or AIRSIM_TIMEOUT (0 < timeout <= 30)")
    weather = AirSimWeatherService(app.config["AIRSIM_HOST"], app.config["AIRSIM_PORT"],
                                   app.config["AIRSIM_TIMEOUT"])
    app.extensions["weather_service"] = weather
    manager = None
    if app.config["SCENE_CONTROL_ENABLED"]:
        config = app.config["SCENE_CONFIG"] or SceneConfig(rpc_port=app.config["AIRSIM_PORT"])
        if app.config["AIRSIM_HOST"] not in ("127.0.0.1", "localhost"):
            raise ValueError("Scene management only supports a local AirSim RPC")
        if config.rpc_port != app.config["AIRSIM_PORT"]:
            raise ValueError("Scene and weather RPC ports must match")
        manager = EnvironmentService(Path(__file__).parent, config, weather)
    app.extensions["environment_service"] = manager

    url = urlsplit(app.config["XPRA_URL"])
    if url.scheme not in ("http", "https") or not url.hostname:
        raise ValueError("XPRA_URL must be an absolute HTTP or HTTPS URL")
    if url.username or url.password or any(key.lower() in ("password", "token")
                                          for key, _ in parse_qsl(url.query)):
        raise ValueError("XPRA_URL must not contain credentials")

    @app.before_request
    def reject_cross_origin_writes():
        if request.method == "POST" and request.path.startswith("/api/"):
            origin = request.headers.get("Origin")
            if request.headers.get("Sec-Fetch-Site") == "cross-site" or (
                origin and origin != f"{request.scheme}://{request.host}"
            ):
                return jsonify(error="cross_origin", message="不允许跨来源控制请求"), 403

    @app.after_request
    def prevent_stale_status(response):
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index():
        return render_template("index.html", xpra_url=app.config["XPRA_URL"],
                               scene_control=manager is not None)

    @app.get("/api/health")
    def health():
        return jsonify(status="ok", backend="flask")

    @app.get("/api/config")
    def configuration():
        return jsonify(xpra_url=app.config["XPRA_URL"], display_mode="xpra-iframe",
                       flight_control_available=False, weather_control_available=True,
                       scene_control_available=manager is not None)

    @app.get("/api/scenes")
    def scenes():
        return jsonify(scenes=scene_list(), enabled=manager is not None)

    @app.get("/api/status")
    def status():
        if manager is None:
            return jsonify(phase="disabled", scene=None, instance_id=None, weather=None,
                           stage="场景管理未启用", error=None)
        return jsonify(manager.snapshot())

    @app.post("/api/switch")
    def switch():
        if manager is None:
            return jsonify(error="disabled", message="场景管理未启用"), 503
        payload = request.get_json(silent=True)
        key = payload.get("scene_id") if isinstance(payload, dict) else None
        try:
            state, accepted = manager.submit(key)
        except ValueError as error:
            return jsonify(error="invalid_scene", message=str(error)), 400
        except SceneBusy as error:
            return jsonify(error="environment_busy", message=str(error)), 409
        except SceneError as error:
            return jsonify(error="environment_error", message=str(error)), 503
        return jsonify(state), 202 if accepted else 200

    @app.post("/api/cleanup")
    def cleanup():
        if manager is None:
            return jsonify(error="disabled", message="场景管理未启用"), 503
        try:
            state, _ = manager.submit(cleanup=True)
        except SceneBusy as error:
            return jsonify(error="environment_busy", message=str(error)), 409
        except SceneError as error:
            return jsonify(error="environment_error", message=str(error)), 503
        return jsonify(state), 202

    @app.get("/api/airsim")
    def airsim_status():
        if manager and manager.snapshot()["phase"] != "ready":
            return jsonify(connected=False, message="请先启动并等待场景就绪"), 503
        try:
            weather.probe()
        except WeatherUnavailable as error:
            return jsonify(connected=False, message=str(error)), 503
        return jsonify(connected=True, message="AirSim RPC 已连接")

    @app.post("/api/weather")
    def set_weather():
        payload = request.get_json(silent=True)
        selected = payload.get("weather") if isinstance(payload, dict) else None
        if not isinstance(selected, str) or selected not in WEATHER_PRESETS:
            return jsonify(error="invalid_weather", message="请选择晴朗、雨天、雾天或雪天"), 400
        try:
            instance = manager.apply_weather(selected, payload.get("instance_id")) if manager else None
            if manager is None:
                weather.apply(selected)
        except (WeatherBusy, SceneBusy) as error:
            return jsonify(error="environment_busy" if manager else "weather_busy", message=str(error)), 409
        except WeatherUnavailable as error:
            app.logger.warning("Weather RPC failed: %s", error)
            return jsonify(error="airsim_unavailable", message=str(error)), 503
        result = dict(weather=selected, status="applied")
        if manager:
            result["instance_id"] = instance
        return jsonify(result)

    @app.errorhandler(404)
    def not_found(error):
        return jsonify(error="not_found"), 404

    @app.errorhandler(405)
    def method_not_allowed(error):
        return jsonify(error="method_not_allowed"), 405
    return app

app = create_app()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AirSim Flight Console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5173)
    parser.add_argument("--display", default=":100")
    parser.add_argument("--tcp-port", type=int, default=10000)
    parser.add_argument("--rpc-port", type=int, default=41451)
    parser.add_argument("--settings", default="/data/airsim/settings.json")
    for option, default in (("port-release-wait",10), ("kill-wait",5),
                            ("tcp-ready-timeout",30), ("rpc-ready-timeout",120),
                            ("poll-interval",0.2)):
        parser.add_argument("--" + option, type=float, default=default)
    args = parser.parse_args()
    config = SceneConfig(display=args.display, tcp_port=args.tcp_port, rpc_port=args.rpc_port,
                         settings=args.settings, port_release_wait=args.port_release_wait,
                         kill_wait=args.kill_wait, tcp_ready_timeout=args.tcp_ready_timeout,
                         rpc_ready_timeout=args.rpc_ready_timeout, poll_interval=args.poll_interval)
    app = create_app({"SCENE_CONTROL_ENABLED":True, "SCENE_CONFIG":config,
                      "AIRSIM_PORT":config.rpc_port})
    manager = app.extensions["environment_service"]
    def terminate(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    try:
        manager.submit(cleanup=True)
        app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
    finally:
        manager.shutdown()
