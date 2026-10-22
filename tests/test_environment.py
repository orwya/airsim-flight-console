import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from app import create_app
from environment_service import EnvironmentService, SceneBusy, SceneConfig
from scene_launcher import SCENES, get_commands
from scene_processes import ProcessRuntime, SceneError

ROOT = Path(__file__).resolve().parents[1]

class SceneTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".runtime").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".runtime")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        settings = self.root / "source.json"
        settings.write_text('{"SimMode":"Multirotor"}')
        self.config = SceneConfig(settings=str(settings), port_release_wait=.05,
                                  kill_wait=.05, rpc_ready_timeout=.1, tcp_ready_timeout=.1,
                                  poll_interval=.01)
        self.weather = MagicMock()
        self.service = EnvironmentService(self.root, self.config, self.weather)
        self.runtime = MagicMock()
        self.runtime.path = self.root
        (self.root / "logs").mkdir()
        self.runtime.sandbox.side_effect = lambda args: args
        self.runtime.stop.return_value = []
        self.runtime.window_ready.return_value = True
        self.runtime.holders.return_value = {100:("airsimnh", None)}
        self.service.startup_weather = self.weather
        self.service.runtime = self.runtime
        self.service.lease = object()  # No production lock or launch in unit tests.

    def run_switch(self, error=None):
        process = MagicMock(pid=123456)
        process.poll.return_value = None
        process.wait.return_value = 0
        if error:
            self.weather.apply.side_effect = error
        with patch("environment_service.port_alive", return_value=True), \
             patch("environment_service.subprocess.Popen", return_value=process), \
             patch("environment_service.identity", return_value=(1,os.getuid(),"fake",[])), \
             patch("environment_service.kill_verified_tree") as kill:
            state, accepted = self.service.submit("airsimnh")
            self.service.worker.join(2)
            self.assertFalse(self.service.worker.is_alive())
            return self.service.snapshot(False), process, kill

    def test_ready_requires_probe_window_and_clear(self):
        result, process, kill = self.run_switch()
        self.assertEqual(result["phase"], "ready")
        self.assertEqual(result["scene"], "airsimnh")
        self.assertEqual(result["weather"], "clear")
        self.weather.probe.assert_called()
        self.runtime.window_ready.assert_called_with("airsimnh")
        self.weather.apply.assert_called_once_with("clear")
        kill.assert_not_called()

    def test_initialization_failure_cleans_only_new_launch_and_allows_retry(self):
        result, process, kill = self.run_switch(SceneError("weather failure"))
        self.assertEqual(result["phase"], "error")
        self.assertIsNone(result["scene"])
        kill.assert_called_once()
        self.assertFalse(self.service.gate.locked())

    def test_foreign_holder_preserves_existing_process(self):
        previous = MagicMock()
        self.service.process = previous
        self.service.state.update(phase="error", scene="msbuild2018")
        self.runtime.stop.side_effect = SceneError("foreign owner")
        with patch("environment_service.port_alive", return_value=True):
            self.service.submit("airsimnh")
            self.service.worker.join(2)
        previous.wait.assert_not_called()
        self.assertIs(self.service.process, previous)
        self.assertIn("foreign", self.service.state["error"])

    def test_invalid_scene_and_concurrent_request_do_not_touch_processes(self):
        for key in ("unknown", "../airsim", [], None):
            with self.assertRaises(ValueError):
                self.service.submit(key)
        self.service.gate.acquire()
        with self.assertRaises(SceneBusy):
            self.service.submit("airsimnh")
        self.service.gate.release()
        self.runtime.stop.assert_not_called()

    def test_weather_rejects_old_instance_busy_and_not_ready(self):
        self.service.state.update(phase="ready", scene="airsimnh", instance_id="current")
        with patch("environment_service.port_alive",return_value=True):
            with self.assertRaises(SceneBusy):
                self.service.apply_weather("rain", "old")
            self.service.apply_weather("rain", "current")
        self.assertEqual(self.service.state["weather"], "rain")
        self.service.state["phase"] = "starting"
        with self.assertRaises(SceneBusy):
            self.service.apply_weather("clear", "current")

    def test_missing_rpc_invalidates_ready_status(self):
        self.service.state.update(phase="ready",scene="airsimnh",instance_id="current")
        with patch("environment_service.port_alive",side_effect=[True,False]):
            state = self.service.snapshot()
        self.assertEqual(state["phase"],"error")
        self.assertIsNone(state["scene"])

    def test_config_rejects_unsafe_values(self):
        for kwargs in ({"display":":100; kill"}, {"tcp_port":41451},
                       {"rpc_port":0}, {"poll_interval":float("nan")}):
            with self.assertRaises(ValueError):
                SceneConfig(**kwargs)

    def test_templates_do_not_run_chmod_scripts_or_browser_shell(self):
        for key, scene in SCENES.items():
            start, stop = get_commands(key, self.config, self.root)
            self.assertIn("--daemon=no",start)
            self.assertEqual(stop[2],"socket://"+str(self.root/"sessions"/"100"/"socket"))
            child = next(arg for arg in start if arg.startswith("--start-child="))
            self.assertIn(scene.executable,child)
            self.assertNotIn(".sh",child)
            self.assertIn("-settings=",child)

    def test_unknown_listener_is_not_killed(self):
        runtime = ProcessRuntime(self.root,self.config)
        with patch("scene_processes.listener_pids",return_value={111}), \
             patch.object(runtime,"owned_scene",return_value=None), \
             patch("scene_processes.identity",return_value=(1,os.getuid(),"/unknown",["unknown"])), \
             patch("scene_processes.kill_verified_tree") as kill:
            with self.assertRaises(SceneError):
                runtime.stop(["xpra","stop",":100"])
            kill.assert_not_called()

    def test_xpra_setproctitle_is_recognized_only_with_exact_runtime(self):
        runtime = ProcessRuntime(self.root,self.config)
        exe = SCENES["airsimnh"].executable
        title = f"/usr/bin/xpra start :100 --start-child={exe} AirSimNH --socket-dir={runtime.path / 'sockets'}"
        with patch("scene_processes.identity",return_value=(1,os.getuid(),os.path.realpath("/usr/bin/python3"),[title])):
            self.assertEqual(runtime.owned_scene(111),"airsimnh")
        with patch("scene_processes.identity",return_value=(1,os.getuid(),os.path.realpath("/usr/bin/python3"),[title.replace(":100",":1000")])):
            self.assertIsNone(runtime.owned_scene(111))

    def test_window_mapping_is_limited_to_verified_scene_pid(self):
        runtime = ProcessRuntime(self.root,self.config)
        info = MagicMock(stdout="windows.200.pid=500\n",returncode=0)
        with patch("scene_processes.subprocess.run",return_value=info) as run, \
             patch.object(runtime,"owned_scene",return_value="airsimnh"):
            self.assertTrue(runtime.window_ready("airsimnh"))
            self.assertEqual(len(run.call_args_list),3)
            self.assertIn("map",run.call_args_list[1].args[0])
            self.assertIn("focus",run.call_args_list[2].args[0])
        with patch("scene_processes.subprocess.run",return_value=info) as run, \
             patch.object(runtime,"owned_scene",return_value=None):
            self.assertFalse(runtime.window_ready("airsimnh"))
            self.assertEqual(run.call_count,1)

class SceneApiTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING":True,"SCENE_CONTROL_ENABLED":True})
        self.client = self.app.test_client()
        self.manager = self.app.extensions["environment_service"]

    def test_fixed_scene_catalog_and_no_add_controls(self):
        response = self.client.get("/api/scenes")
        self.assertEqual([s["id"] for s in response.json["scenes"]],list(SCENES))
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('data-scene-control="true"',html)
        self.assertNotIn('data-add="scene"',html)
        self.assertIn('id="scene-overlay"',html)

    def test_routes_reject_invalid_busy_and_cross_origin(self):
        self.assertEqual(self.client.post("/api/switch",json={"scene_id":"bad"}).status_code,400)
        with patch.object(self.manager,"submit",side_effect=SceneBusy("busy")):
            self.assertEqual(self.client.post("/api/switch",json={"scene_id":"airsimnh"}).status_code,409)
        for path in ("/api/switch","/api/cleanup","/api/weather"):
            response=self.client.post(path,json={},headers={"Origin":"http://evil.test"})
            self.assertEqual(response.status_code,403)
        self.assertEqual(self.client.get("/api/cleanup").status_code,405)

    def test_weather_closed_before_ready_and_uses_instance(self):
        response=self.client.post("/api/weather",json={"weather":"rain","instance_id":"stale"})
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.client.get("/api/airsim").status_code,503)
        with patch.object(self.manager,"apply_weather",return_value="live") as apply:
            result=self.client.post("/api/weather",json={"weather":"snow","instance_id":"live"})
            apply.assert_called_once_with("snow","live")
            self.assertEqual(result.json["instance_id"],"live")
