"""Planner 解耦：后端只按 --planner-port 拼出地址，不代理、不探活、不参与场景门禁。"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import create_app
from cli import build_parser
from config import Settings, build_settings
from environment_service import EnvironmentService, SceneBusy
from weather_service import AirSimWeatherService

ROOT = Path(__file__).resolve().parents[1]


def settings_for(root, **overrides):
    base = dict(root=Path(root), airsim_settings=str(Path(root) / "missing.json"))
    base.update(overrides)
    return Settings(**base)


class PlannerUrlTests(unittest.TestCase):
    def test_cli_exposes_only_a_port(self):
        args = build_parser().parse_args(["--ip", "172.27.137.174", "--planner-port", "8080"])
        settings = build_settings(args)
        self.assertEqual(settings.planner_port, 8080)
        self.assertEqual(settings.planner_url, "http://172.27.137.174:8080")
        self.assertEqual(build_settings(build_parser().parse_args([])).planner_url, "")
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["--planner-url", "http://host:8080"])

    def test_invalid_ports_are_rejected(self):
        for value in (0, 65536, True, 5173, 10000, 41451):
            with self.subTest(value=value), TemporaryDirectory() as temp, \
                    self.assertRaises(ValueError):
                settings_for(temp, planner_port=value)

    def test_page_and_config_carry_the_derived_url(self):
        with TemporaryDirectory() as temp:
            app = create_app(settings_for(temp, ip="172.27.137.174", planner_port=8080))
            client = app.test_client()
            html = client.get("/").get_data(as_text=True)
            self.assertIn('data-planner-url="http://172.27.137.174:8080"', html)
            config = client.get("/api/config").json
            self.assertEqual(config["planner_url"], "http://172.27.137.174:8080")
            for gone in ("planner_timeout", "planner_control_available", "flight_control_available"):
                self.assertNotIn(gone, config)

    def test_removed_proxy_routes_are_gone(self):
        with TemporaryDirectory() as temp:
            client = create_app(settings_for(temp)).test_client()
            for path in ("/api/planner/status", "/api/planner/logs",
                         "/api/planner/tasks", "/api/planner/stop"):
                self.assertEqual(client.get(path).status_code, 404, path)
                self.assertEqual(client.post(path).status_code, 404, path)

    def test_backend_has_no_planner_client(self):
        self.assertFalse((ROOT / "planner_service.py").exists())
        for name in ("app.py", "config.py", "cli.py", "environment_service.py"):
            source = (ROOT / name).read_text()
            for token in ("http.client", "PlannerService", "wait_idle", "submit_task"):
                self.assertNotIn(token, source, f"{name} 不应再包含 {token}")


class DecouplingTests(unittest.TestCase):
    def scenes(self, temp, **overrides):
        settings = settings_for(temp, **overrides)
        return EnvironmentService(
            settings, AirSimWeatherService("127.0.0.1", settings.rpc_port, 0.1)
        )

    def test_scene_switch_succeeds_while_planner_is_unreachable(self):
        # 端口 9 上没有服务：后端只要还尝试连接，场景就会落到 error。
        with TemporaryDirectory() as temp:
            scenes = self.scenes(temp, planner_port=9)
            with patch("environment_service.port_alive", return_value=True), \
                 patch.object(scenes, "_check_scene_files"), \
                 patch.object(scenes, "_stop_current"), \
                 patch.object(scenes, "_launch", return_value=True), \
                 patch.object(scenes, "_wait_ready"), \
                 patch.object(scenes.startup_weather, "apply"):
                scenes.gate.acquire()
                scenes._run("abandonedpark", False)
            self.assertEqual(scenes.state["phase"], "ready")
            self.assertEqual(scenes.state["scene"], "abandonedpark")
            self.assertIsNone(scenes.state["error"])
            self.assertFalse(scenes.gate.locked())

    def test_cleanup_succeeds_while_planner_is_unreachable(self):
        with TemporaryDirectory() as temp:
            scenes = self.scenes(temp, planner_port=9)
            with patch.object(scenes, "_stop_current"), \
                 patch.object(scenes, "_cleanup_launch"):
                scenes.gate.acquire()
                scenes._run(None, True)
            self.assertEqual(scenes.state["phase"], "idle")
            self.assertIsNone(scenes.state["error"])
            self.assertFalse(scenes.gate.locked())

    def test_second_scene_operation_is_rejected_immediately(self):
        with TemporaryDirectory() as temp:
            scenes = self.scenes(temp)
            self.assertTrue(scenes.gate.acquire(blocking=False))
            try:
                with self.assertRaises(SceneBusy):
                    scenes.submit("airsimnh")
            finally:
                scenes.gate.release()

    def test_scene_service_has_no_planner_attribute(self):
        with TemporaryDirectory() as temp:
            scenes = self.scenes(temp, planner_port=9)
            self.assertFalse(hasattr(scenes, "planner"))
            self.assertFalse(hasattr(scenes, "submit_task"))


if __name__ == "__main__":
    unittest.main()