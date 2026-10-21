"""Flask entry point for the AEROLYTE console."""

import argparse
import os
from urllib.parse import parse_qsl, urlsplit

from flask import Flask, jsonify, render_template, request

from weather_service import AirSimWeatherService, WEATHER_PRESETS, WeatherBusy, WeatherUnavailable


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        XPRA_URL=os.environ.get("XPRA_URL", "http://172.27.137.174:10000/"),
        AIRSIM_HOST=os.environ.get("AIRSIM_HOST", "127.0.0.1"),
        AIRSIM_PORT=int(os.environ.get("AIRSIM_PORT", "41451")),
        AIRSIM_TIMEOUT=float(os.environ.get("AIRSIM_TIMEOUT", "5")),
    )
    if test_config:
        app.config.update(test_config)

    if not 1 <= app.config["AIRSIM_PORT"] <= 65535 or not 0 < app.config["AIRSIM_TIMEOUT"] <= 30:
        raise ValueError("Invalid AIRSIM_PORT or AIRSIM_TIMEOUT (0 < timeout <= 30)")
    app.extensions["weather_service"] = AirSimWeatherService(
        app.config["AIRSIM_HOST"], app.config["AIRSIM_PORT"], app.config["AIRSIM_TIMEOUT"]
    )

    url = urlsplit(app.config["XPRA_URL"])
    if url.scheme not in ("http", "https") or not url.hostname:
        raise ValueError("XPRA_URL must be an absolute HTTP or HTTPS URL")
    if url.username or url.password or any(
        key.lower() in ("password", "token")
        for key, _ in parse_qsl(url.query)
    ):
        raise ValueError("XPRA_URL must not contain credentials")

    @app.get("/")
    def index():
        return render_template("index.html", xpra_url=app.config["XPRA_URL"])

    @app.get("/api/health")
    def health():
        # Application health only; this does not probe the remote xpra session.
        return jsonify(status="ok", backend="flask")

    @app.get("/api/config")
    def configuration():
        return jsonify(
            xpra_url=app.config["XPRA_URL"],
            display_mode="xpra-iframe",
            flight_control_available=False,
            weather_control_available=True,
        )

    @app.get("/api/airsim")
    def airsim_status():
        try:
            app.extensions["weather_service"].probe()
        except WeatherUnavailable as error:
            return jsonify(connected=False, message=str(error)), 503
        return jsonify(connected=True, message="AirSim RPC 已连接")

    @app.post("/api/weather")
    def set_weather():
        payload = request.get_json(silent=True)
        weather = payload.get("weather") if isinstance(payload, dict) else None
        if not isinstance(weather, str) or weather not in WEATHER_PRESETS:
            return jsonify(error="invalid_weather", message="请选择晴朗、雨天、雾天或雪天"), 400
        try:
            app.extensions["weather_service"].apply(weather)
        except WeatherBusy as error:
            return jsonify(error="weather_busy", message=str(error)), 409
        except WeatherUnavailable as error:
            app.logger.warning("Weather RPC failed: %s", error)
            return jsonify(error="airsim_unavailable", message=str(error)), 503
        return jsonify(weather=weather, status="applied")

    @app.errorhandler(404)
    def not_found(error):
        return jsonify(error="not_found"), 404

    @app.errorhandler(405)
    def method_not_allowed(error):
        return jsonify(error="method_not_allowed"), 405

    return app


app = create_app()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AEROLYTE Flask console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5173)
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=False)
