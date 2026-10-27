"""Flask 装配与路由。所有配置来自 config.Settings，不读取环境变量。"""

from dataclasses import dataclass

from flask import Flask, jsonify, render_template, request

from config import LOOPBACK, Settings
from environment_service import EnvironmentService, SceneBusy
from planner_service import PlannerService, PlannerError
from scene_launcher import scene_list
from scene_processes import SceneError
from weather_service import (
    AirSimWeatherService,
    WEATHER_PRESETS,
    WeatherBusy,
    WeatherUnavailable,
)


@dataclass(frozen=True)
class Services:
    settings: Settings
    weather: AirSimWeatherService
    scenes: EnvironmentService
    planner: PlannerService


def create_app(settings: Settings) -> Flask:
    app = Flask(__name__)
    weather = AirSimWeatherService(LOOPBACK, settings.rpc_port, settings.airsim_timeout)
    planner = PlannerService(settings)
    scenes = EnvironmentService(settings, weather)
    scenes.planner = planner if planner.enabled else None
    services = Services(settings, weather, scenes, planner)
    app.extensions["services"] = services
    _install_hooks(app)
    _install_routes(app, services)
    _install_planner_routes(app, services)
    _install_error_handlers(app)
    return app


def _install_hooks(app):
    @app.before_request
    def reject_cross_origin_writes():
        if request.method == "POST" and request.path.startswith("/api/"):
            origin = request.headers.get("Origin")
            if request.headers.get("Sec-Fetch-Site") == "cross-site" or (
                origin and origin != f"{request.scheme}://{request.host}"
            ):
                return jsonify(
                    error="cross_origin", message="不允许跨来源控制请求"
                ), 403

    @app.after_request
    def prevent_stale_status(response):
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response


def _scene_failure(error):
    if isinstance(error, ValueError):
        return jsonify(error="invalid_scene", message=str(error)), 400
    if isinstance(error, SceneBusy):
        return jsonify(error="environment_busy", message=str(error)), 409
    return jsonify(error="environment_error", message=str(error)), 503


def _install_routes(app, services: Services):
    settings, weather, scenes = services.settings, services.weather, services.scenes

    @app.get("/")
    def index():
        return render_template("index.html", xpra_url=settings.xpra_url, planner_enabled=services.planner.enabled, planner_timeout=settings.planner_timeout)

    @app.get("/api/health")
    def health():
        return jsonify(status="ok", backend="flask")

    @app.get("/api/config")
    def configuration():
        return jsonify(
            console_url=settings.console_url,
            xpra_url=settings.xpra_url,
            display_mode="xpra-iframe",
            flight_control_available=services.planner.enabled,
            planner_control_available=services.planner.enabled,
            planner_timeout=settings.planner_timeout,
            weather_control_available=True,
        )

    @app.get("/api/scenes")
    def scenes_catalog():
        return jsonify(scenes=scene_list())

    @app.get("/api/status")
    def status():
        return jsonify(scenes.snapshot())

    @app.post("/api/switch")
    def switch():
        payload = request.get_json(silent=True)
        key = payload.get("scene_id") if isinstance(payload, dict) else None
        try:
            state, accepted = scenes.submit(key)
        except (ValueError, SceneBusy, SceneError) as error:
            return _scene_failure(error)
        return jsonify(state), 202 if accepted else 200

    @app.post("/api/cleanup")
    def cleanup():
        try:
            state, _ = scenes.submit(cleanup=True)
        except (SceneBusy, SceneError) as error:
            return _scene_failure(error)
        return jsonify(state), 202

    @app.get("/api/airsim")
    def airsim_status():
        if scenes.snapshot()["phase"] != "ready":
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
            return jsonify(
                error="invalid_weather", message="请选择晴朗、雨天、雾天或雪天"
            ), 400
        try:
            instance = scenes.apply_weather(selected, payload.get("instance_id"))
        except (WeatherBusy, SceneBusy) as error:
            return jsonify(error="environment_busy", message=str(error)), 409
        except WeatherUnavailable as error:
            app.logger.warning("Weather RPC failed: %s", error)
            return jsonify(error="airsim_unavailable", message=str(error)), 503
        return jsonify(weather=selected, status="applied", instance_id=instance)


def _install_planner_routes(app, services):
    planner, scenes = services.planner, services.scenes

    @app.get("/api/planner/status")
    def planner_status():
        # Preserve HTTP errors and object bodies from the upstream.
        if not planner.enabled:
            raise PlannerError("Planner 未配置", 503, "planner_disabled")
        return _planner_response(planner.request("GET", "/api/status"))

    @app.get("/api/planner/logs")
    def planner_logs():
        text = request.args.get("cursor", "0")
        if not text.isascii() or not text.isdecimal():
            return jsonify(detail=[{"loc": ["query", "cursor"], "msg": "cursor 必须为非负整数"}]), 422
        if len(text) > 19:
            return jsonify(detail=[{"loc": ["query", "cursor"], "msg": "cursor 过大"}]), 422
        return _planner_response(planner.logs(int(text)))

    @app.post("/api/planner/tasks")
    def planner_tasks():
        payload = request.get_json(silent=True)
        if (not isinstance(payload, dict) or set(payload) != {"instruction"}
                or not isinstance(payload.get("instruction"), str)
                or not payload["instruction"].strip()):
            return jsonify(detail=[{"loc": ["body", "instruction"], "msg": "只允许非空字符串 instruction"}]), 422
        try:
            result = scenes.submit_task({"instruction": payload["instruction"].strip()})
        except SceneBusy as error:
            return jsonify(error="environment_busy", message=str(error)), 409
        return _planner_response(result)

    @app.post("/api/planner/stop")
    def planner_stop():
        # Stopping remains possible when the scene is unavailable.
        return _planner_response(planner.stop())


def _planner_response(result):
    body, status = result
    return jsonify(body), status


def _install_error_handlers(app):
    @app.errorhandler(PlannerError)
    def planner_error(error):
        return jsonify(error.body), error.status

    @app.errorhandler(404)
    def not_found(error):
        return jsonify(error="not_found"), 404

    @app.errorhandler(405)
    def method_not_allowed(error):
        return jsonify(error="method_not_allowed"), 405


if __name__ == "__main__":
    from cli import main

    main()
