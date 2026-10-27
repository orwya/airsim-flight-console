"""Planner transport and scene coordination; never connects to a real drone."""
import json
import socket
import threading
import time
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from app import create_app
from cli import build_parser
from config import Settings, build_settings
from environment_service import EnvironmentService
from planner_service import PlannerError, PlannerService

ROOT = Path(__file__).resolve().parents[1]


class FakePlanner(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.respond()

    def do_POST(self):
        self.respond()

    def respond(self):
        data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.requests.append((self.command, self.path, data))
        status, body = self.server.responses.get(self.path.split("?")[0], (200, {"state": "idle"}))
        if self.server.delay:
            time.sleep(self.server.delay)
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass


class PlannerTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".runtime").mkdir(exist_ok=True)
        self.temp = TemporaryDirectory(dir=ROOT / ".runtime")
        self.addCleanup(self.temp.cleanup)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakePlanner)
        self.server.responses = {}
        self.server.requests = []
        self.server.delay = 0
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.settings = Settings(root=Path(self.temp.name), planner_port=self.server.server_port,
                                 planner_timeout=0.2, rpc_ready_timeout=0.1, poll_interval=0.005)
        self.app = create_app(self.settings)
        self.client = self.app.test_client()
        self.services = self.app.extensions["services"]
        self.scenes = self.services.scenes
        self.scenes.runtime = MagicMock()
        self.scenes.lease = object()
        self.scenes._set(phase="ready", scene="airsimnh", instance_id="current", weather="rain")

    def task(self, **kwargs):
        with patch("environment_service.port_alive", return_value=True):
            return self.client.post("/api/planner/tasks", json=kwargs or {"instruction": "飞到红色汽车附近"})

    def run_scene(self, cleanup=False):
        with patch("environment_service.port_alive", return_value=True), \
             patch.object(self.scenes, "_check_scene_files"), \
             patch.object(self.scenes, "_stop_current") as stop, \
             patch.object(self.scenes, "_launch", return_value=True), \
             patch.object(self.scenes, "_wait_ready"), \
             patch.object(self.scenes, "_cleanup_launch"), \
             patch.object(self.scenes.startup_weather, "apply"):
            self.scenes.gate.acquire()
            self.scenes._run("abandonedpark", cleanup)
            return stop

    def test_cli_and_port_configuration(self):
        args = build_parser().parse_args(["--planner-port", str(self.server.server_port)])
        self.assertEqual(build_settings(args).planner_port, self.server.server_port)
        self.assertIsNone(Settings().planner_port)
        for kwargs in ({"planner_port": 0}, {"planner_port": True}, {"planner_port": 65536},
                       {"planner_port": 5173}, {"planner_port": 10000}, {"planner_port": 41451},
                       {"planner_timeout": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Settings(**kwargs)

    def test_disabled_feature_and_template(self):
        app = create_app(Settings())
        client = app.test_client()
        self.assertFalse(client.get("/api/config").json["planner_control_available"])
        self.assertEqual(client.get("/api/planner/status").json["error"], "planner_disabled")
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('data-planner-enabled="true"', html)
        self.assertIn("/static/planner.js", html)
        self.assertIn('id="planner-stop"', html)

    def test_task_unicode_and_exact_payload(self):
        self.server.responses["/api/tasks"] = (202, {"task_id": "opaque-id", "state": "running"})
        response = self.task()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json["task_id"], "opaque-id")
        self.assertEqual(json.loads(self.server.requests[-1][2]), {"instruction": "飞到红色汽车附近"})
        self.assertFalse(self.scenes.gate.locked())

    def test_invalid_payloads_never_reach_planner(self):
        for payload in (None, [], "encoded-json", {}, {"instruction": " "},
                        {"instruction": 12}, {"instruction": "fly", "model": "x"}):
            r = self.client.post("/api/planner/tasks", json=payload)
            self.assertEqual(r.status_code, 422)
            self.assertIsInstance(r.json["detail"], list)
        self.assertEqual(self.server.requests, [])

    def test_upstream_rejections_are_preserved(self):
        for status, body in ((409, {"detail": {"state": "running", "message": "busy"}}),
                             (503, {"detail": {"state": "connecting", "message": "connecting"}}),
                             (422, {"detail": [{"loc": ["body"], "msg": "bad"}]})):
            self.server.responses["/api/tasks"] = (status, body)
            r = self.task()
            self.assertEqual((r.status_code, r.json), (status, body))

    def test_busy_planner_prevents_forwarding_task(self):
        for state in ("running", "stopping", "connecting"):
            self.server.responses["/api/status"] = (200, {"state": state})
            r = self.task()
            self.assertEqual(r.status_code, 503 if state == "connecting" else 409)
        self.assertFalse(any(path == "/api/tasks" for _, path, _ in self.server.requests))

    def test_unready_scene_rejects_task_without_upstream(self):
        self.scenes._set(phase="starting")
        self.assertEqual(self.task().status_code, 409)
        self.assertEqual(self.server.requests, [])

    def test_stop_works_with_failed_scene_and_has_no_body(self):
        self.scenes._set(phase="error")
        self.server.responses["/api/stop"] = (202, {"state": "stopping"})
        r = self.client.post("/api/planner/stop")
        self.assertEqual(r.status_code, 202)
        self.assertEqual(self.server.requests[-1], ("POST", "/api/stop", b""))

    def test_log_cursor_forwarding_and_validation(self):
        body = {"items": [], "next_cursor": 102, "truncated": False}
        self.server.responses["/api/logs"] = (200, body)
        self.assertEqual(self.client.get("/api/planner/logs?cursor=100").json, body)
        self.assertEqual(self.server.requests[-1][1], "/api/logs?cursor=100")
        for cursor in ("-1", "1.5", "x", "9" * 30):
            self.assertEqual(self.client.get("/api/planner/logs?cursor=" + cursor).status_code, 422)

    def test_transport_failures_are_not_retried(self):
        self.server.delay = 0.08
        planner = PlannerService(replace(self.settings, planner_timeout=0.015))
        with self.assertRaises(PlannerError) as caught:
            planner.submit({"instruction": "fly"})
        self.assertEqual(caught.exception.status, 504)
        self.assertEqual(len(self.server.requests), 1)
        self.server.delay = 0
        self.server.responses["/api/status"] = (200, b"not json")
        self.assertEqual(self.client.get("/api/planner/status").status_code, 502)
        with patch("planner_service.http.client.HTTPConnection.request", side_effect=ConnectionRefusedError):
            self.assertEqual(self.client.get("/api/planner/status").status_code, 502)

    def test_stop_failure_or_connecting_timeout_preserves_old_scene(self):
        for state in ("running", "connecting"):
            planner = MagicMock()
            planner.wait_idle.side_effect = PlannerError("stop not confirmed")
            self.scenes.planner = planner
            stop = self.run_scene()
            stop.assert_not_called()
            self.assertEqual(self.scenes.state["scene"], "airsimnh")
            self.assertEqual(self.scenes.state["instance_id"], "current")
            self.assertEqual(self.scenes.state["phase"], "error")
            self.assertFalse(self.scenes.gate.locked())

    def test_wait_idle_does_not_trust_stop_response_alone(self):
        planner = self.services.planner
        with patch.object(planner, "status", return_value={"state": "connecting"}), \
             patch.object(planner, "stop", return_value=({"state": "connecting"}, 200)) as stop:
            with self.assertRaises(PlannerError) as caught:
                planner.wait_idle(lambda: None, lambda text: None, before_stop=True)
            self.assertEqual(caught.exception.body["error"], "planner_wait_timeout")
            stop.assert_called_once()

    def test_running_stop_then_poll_idle(self):
        planner = self.services.planner
        with patch.object(planner, "status", side_effect=[{"state": "running"}, {"state": "stopping"}, {"state": "idle"}]), \
             patch.object(planner, "stop", return_value=({"state": "stopping"}, 202)) as stop:
            planner.wait_idle(lambda: None, lambda text: None, before_stop=True)
            stop.assert_called_once()

    def test_bootstrap_connecting_without_old_scene_does_not_deadlock(self):
        planner = self.services.planner
        with patch.object(planner, "status", return_value={"state": "connecting"}), \
             patch.object(planner, "stop") as stop:
            planner.wait_idle(lambda: None, lambda text: None, before_stop=True, has_scene=False)
            stop.assert_not_called()

    def test_scene_operation_waits_for_stop_and_new_connection(self):
        events = []
        planner = MagicMock()
        planner.wait_idle.side_effect = lambda *a, **kw: events.append("stop-planner" if kw.get("before_stop") else "wait-new")
        self.scenes.planner = planner
        with patch("environment_service.port_alive", return_value=True), \
             patch.object(self.scenes, "_check_scene_files"), \
             patch.object(self.scenes, "_stop_current", side_effect=lambda x: events.append("stop-scene")), \
             patch.object(self.scenes, "_launch", return_value=True), \
             patch.object(self.scenes, "_wait_ready"), \
             patch.object(self.scenes.startup_weather, "apply", side_effect=lambda x: events.append("clear")):
            self.scenes.gate.acquire()
            self.scenes._run("abandonedpark", False)
        self.assertEqual(events, ["stop-planner", "stop-scene", "clear", "wait-new"])
        self.assertEqual(self.scenes.state["phase"], "ready")

    def test_submit_and_cleanup_are_serialized(self):
        entered, release, switched = threading.Event(), threading.Event(), threading.Event()
        planner = MagicMock()
        planner.request.return_value = ({"state": "idle"}, 200)
        def submit(payload):
            entered.set()
            release.wait(1)
            return {"state": "running", "task_id": "t"}, 202
        planner.submit.side_effect = submit
        self.scenes.planner = planner
        with patch("environment_service.port_alive", return_value=True), \
             patch.object(self.scenes, "_run", side_effect=lambda *a: (switched.set(), self.scenes.gate.release())):
            task_thread = threading.Thread(target=lambda: self.scenes.submit_task({"instruction": "fly"}))
            task_thread.start()
            self.assertTrue(entered.wait(1))
            scene_thread = threading.Thread(target=lambda: self.scenes.submit(cleanup=True))
            scene_thread.start()
            self.assertFalse(switched.wait(0.03))
            release.set()
            task_thread.join(1)
            scene_thread.join(1)
            self.assertTrue(switched.wait(1))
        self.assertFalse(self.scenes.gate.locked())

    def test_same_origin_hook_and_no_store_apply_to_planner(self):
        r = self.client.post("/api/planner/stop", headers={"Origin": "http://evil.test"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.get("/api/planner/status").headers["Cache-Control"], "no-store")



    def test_foreign_listener_is_checked_before_planner_stop(self):
        planner = MagicMock()
        self.scenes.planner = planner
        self.scenes.runtime.holders.side_effect = RuntimeError("foreign listener")
        stop = self.run_scene(cleanup=True)
        stop.assert_not_called()
        planner.wait_idle.assert_not_called()
        self.assertEqual(self.scenes.state["scene"], "airsimnh")

    def test_malformed_state_fails_closed_and_upstream_status_errors_preserved(self):
        self.server.responses["/api/status"] = (503, {"detail": {"state": "connecting", "message": "wait"}})
        r = self.task()
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json["detail"]["message"], "wait")
        self.assertFalse(any(path == "/api/tasks" for _,path,_ in self.server.requests))
        self.server.responses["/api/status"] = (200, {"state": []})
        self.assertEqual(self.task().status_code, 502)
        with self.assertRaises(PlannerError):
            self.services.planner.status()
