"""Flask entry point for the AEROLYTE console."""

import argparse
import os
from urllib.parse import parse_qsl, urlsplit

from flask import Flask, jsonify, render_template


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        XPRA_URL=os.environ.get("XPRA_URL", "http://172.27.137.174:10000/"),
    )
    if test_config:
        app.config.update(test_config)

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
        )

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
