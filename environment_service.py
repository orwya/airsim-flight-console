"""Serialized scene switching. No untrusted command strings or global process-name kills."""
from dataclasses import dataclass
from pathlib import Path
import fcntl
import json
import math
import os
import re
import subprocess
import threading
import time
import uuid

from scene_launcher import SCENES, get_commands
from scene_processes import ProcessRuntime, SceneError, identity, kill_verified_tree, port_alive
from weather_service import WeatherUnavailable, AirSimWeatherService

BUSY = frozenset(("stopping", "starting", "initializing"))

class SceneBusy(SceneError):
    pass

@dataclass(frozen=True)
class SceneConfig:
    display: str = ":100"
    tcp_port: int = 10000
    rpc_port: int = 41451
    port_release_wait: float = 10
    kill_wait: float = 5
    tcp_ready_timeout: float = 30
    rpc_ready_timeout: float = 120
    poll_interval: float = 0.2
    settings: str = "/data/airsim/settings.json"

    def __post_init__(self):
        if not re.fullmatch(r":[1-9][0-9]{0,4}", self.display):
            raise ValueError("display must be an explicit X11 display such as :100")
        if not all(type(p) is int and 1 <= p <= 65535 for p in (self.tcp_port, self.rpc_port)):
            raise ValueError("invalid scene port")
        if self.tcp_port == self.rpc_port:
            raise ValueError("scene ports must differ")
        for field in ("port_release_wait", "kill_wait", "tcp_ready_timeout", "rpc_ready_timeout", "poll_interval"):
            value = getattr(self, field)
            if not math.isfinite(value) or not 0 < value <= 600:
                raise ValueError(f"invalid {field}")

class EnvironmentService:
    def __init__(self, root, config, weather):
        self.config, self.weather = config, weather
        self.startup_weather = AirSimWeatherService(weather.host, weather.port, 30)
        self.root = Path(root).resolve()
        self.runtime = None
        self.gate = threading.Lock()
        self.state_lock = threading.Lock()
        self.cancel = threading.Event()
        self.worker = None
        self.lease = None
        self.process = None
        self.process_identity = None
        self.state = dict(manager_id=str(uuid.uuid4()), revision=0, phase="idle", scene=None, target_scene=None, previous_scene=None,
                          instance_id=None, operation_id=None, weather=None, error=None, cleanup=[],
                          stage="请选择一个场景启动", completed_operation_id=None)

    def _set(self, **values):
        with self.state_lock:
            self.state.update(values)
            self.state["revision"] += 1

    def snapshot(self, refresh=True):
        with self.state_lock:
            state = dict(self.state)
        tcp = port_alive(self.config.tcp_port)
        rpc = port_alive(self.config.rpc_port)
        if refresh and state["phase"] == "ready" and not (tcp and rpc):
            # Do not race a switch that started during the probe.
            with self.state_lock:
                if self.state["phase"] == "ready" and self.state["instance_id"] == state["instance_id"]:
                    self.state["revision"] += 1
                    self.state.update(phase="error", scene=None, weather=None,
                                      error="场景连接已断开，请重试场景或清理", stage="场景异常退出")
                state = dict(self.state)
        return dict(state, port_tcp=tcp, port_rpc=rpc)

    def _prepare(self):
        if self.runtime is None:
            self.runtime = ProcessRuntime(self.root, self.config)
        if self.lease is None:
            stream = open(self.runtime.path / "manager.lock", "a+")
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                stream.close()
                raise SceneError("另一个场景管理器正在运行")
            try:
                subprocess.run(self.runtime.sandbox(["/usr/bin/true"]), env=self.runtime.env,
                               capture_output=True, timeout=4, check=True)
            except (OSError, subprocess.SubprocessError) as error:
                stream.close()
                raise SceneError("文件系统隔离不可用，未执行场景启停") from error
            self.lease = stream

    def submit(self, scene_id=None, cleanup=False):
        if not cleanup and (not isinstance(scene_id, str) or scene_id not in SCENES):
            raise ValueError("未知场景")
        if not self.gate.acquire(blocking=False):
            raise SceneBusy("场景或天气正在处理中，请稍后重试")
        try:
            self._prepare()
            current = self.snapshot()
            if not cleanup and current["phase"] == "ready" and current["scene"] == scene_id:
                holders = self.runtime.holders()
                if not holders or any(key != scene_id for key,_ in holders.values()):
                    raise SceneError("当前监听进程与所选场景不匹配")
                self.gate.release()
                return current, False
            operation = str(uuid.uuid4())
            self._set(phase="stopping", target_scene=scene_id, previous_scene=current["scene"],
                      operation_id=operation, error=None, cleanup=[], stage="正在检查和停止旧场景")
            self.worker = threading.Thread(target=self._run, args=(scene_id, cleanup),
                                           daemon=True, name="scene-switch")
            self.worker.start()
            return self.snapshot(False), True
        except Exception:
            if self.gate.locked():
                self.gate.release()
            raise

    def _check_cancel(self):
        if self.cancel.is_set():
            raise SceneError("服务正在退出，已取消切换")

    def _wait(self, condition, seconds, description):
        deadline = time.monotonic() + seconds
        last_error = None
        while time.monotonic() < deadline:
            self._check_cancel()
            if self.process and self.process.poll() is not None:
                raise SceneError(f"启动进程已退出（{self.process.returncode}），请检查 .runtime/logs/launcher.log")
            try:
                if condition():
                    return
            except (WeatherUnavailable, subprocess.TimeoutExpired) as error:
                last_error = error
            time.sleep(self.config.poll_interval)
        raise SceneError(f"{description}超时" + (f"：{last_error}" if last_error else ""))

    def _write_settings(self):
        settings = json.loads(Path(self.config.settings).read_text())
        if not isinstance(settings, dict):
            raise SceneError("settings 必须是 JSON 对象")
        settings.update(ApiServerPort=self.config.rpc_port, RpcEnabled=True)
        output = self.runtime.path / "settings.json"
        temporary = output.with_suffix(".tmp")
        temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2))
        temporary.replace(output)

    def _cleanup_launch(self):
        if self.process:
            before = self.process_identity
            now = identity(self.process.pid)
            if before and now and now[:2] == before[:2] and self.process.poll() is None:
                kill_verified_tree(self.process.pid, now)
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            self.process = None
            self.process_identity = None

    def _run(self, scene_id, cleanup):
        launched = False
        try:
            if not cleanup:
                scene = SCENES[scene_id]
                if not Path(scene.executable).is_file() or not os.access(scene.executable, os.X_OK):
                    raise SceneError(f"场景程序缺失或无执行权限：{scene.name}")
                # Preflight before touching the previous scene.
                source = json.loads(Path(self.config.settings).read_text())
                if not isinstance(source, dict):
                    raise SceneError("settings 必须是 JSON 对象，未停止旧场景")
                if not Path("/usr/bin/bwrap").is_file():
                    raise SceneError("缺少 bwrap，无法保证目录外只读；未停止旧场景")
            _, stop = get_commands(scene_id or next(iter(SCENES)), self.config, self.runtime.path)
            actions = self.runtime.stop(stop)
            self._set(cleanup=actions)
            self._cleanup_launch()
            self._set(scene=None, instance_id=None, weather=None)
            if cleanup:
                self._set(phase="idle", target_scene=None, stage="清理完成", error=None,
                          completed_operation_id=self.state["operation_id"])
                return
            self._check_cancel()
            self._write_settings()
            instance = str(uuid.uuid4())
            self._set(phase="starting", instance_id=instance, stage=f"正在启动 {SCENES[scene_id].name}")
            start, _ = get_commands(scene_id, self.config, self.runtime.path)
            with open(self.runtime.path / "logs" / "launcher.log", "a") as log:
                log.write(f"\n--- {time.time()} {scene_id} {instance} ---\n")
                log.flush()
                self.process = subprocess.Popen(self.runtime.sandbox(start), env=self.runtime.env,
                                                cwd=self.root, stdout=log, stderr=subprocess.STDOUT,
                                                stdin=subprocess.DEVNULL, start_new_session=True)
                self.process_identity = identity(self.process.pid)
                launched = True
            self._wait(lambda: port_alive(self.config.tcp_port), self.config.tcp_ready_timeout, "画面端口启动")
            self._set(stage="等待目标场景 RPC 与窗口")
            def rpc_ready():
                if not port_alive(self.config.rpc_port):
                    return False
                holders = self.runtime.holders()
                if not holders or any(key != scene_id for key, _ in holders.values()):
                    raise SceneError("监听进程与目标场景不匹配")
                self.weather.probe()
                return self.runtime.window_ready(scene_id)
            self._wait(rpc_ready, self.config.rpc_ready_timeout, "目标场景 RPC 或窗口就绪")
            self._set(phase="initializing", stage="正在初始化晴朗天气")
            self.startup_weather.apply("clear")
            self._set(phase="ready", scene=scene_id, target_scene=None, weather="clear",
                      error=None, stage="场景已就绪", completed_operation_id=self.state["operation_id"])
        except Exception as error:
            # Clean only the launch owned by this operation, never an unknown port holder.
            if launched:
                self._cleanup_launch()
            self._set(phase="error", target_scene=None, error=str(error)[:1000],
                      stage="操作失败，可重试或清理",
                      completed_operation_id=self.state["operation_id"])
        finally:
            self.gate.release()

    def apply_weather(self, weather, instance_id):
        if not self.gate.acquire(blocking=False):
            raise SceneBusy("场景正在切换或天气正在设置")
        try:
            state = self.snapshot()
            if state["phase"] != "ready":
                raise SceneBusy("场景尚未就绪")
            if not instance_id or instance_id != state["instance_id"]:
                raise SceneBusy("场景实例已变化，请刷新状态后重试")
            try:
                holders = self.runtime.holders()
                if not holders or any(key != state["scene"] for key,_ in holders.values()):
                    raise SceneError("场景监听归属已变化")
            except SceneError as error:
                self._set(phase="error",scene=None,weather=None,error=str(error),stage="场景归属异常")
                raise SceneBusy(str(error)) from error
            try:
                self.weather.apply(weather)
            except WeatherUnavailable:
                self._set(weather=None)
                raise
            self._set(weather=weather)
            return state["instance_id"]
        finally:
            self.gate.release()

    def shutdown(self):
        self.cancel.set()
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=self.config.port_release_wait + self.config.kill_wait + 8)
        if self.lease is not None and self.gate.acquire(timeout=3):
            try:
                _, stop = get_commands(next(iter(SCENES)), self.config, self.runtime.path)
                self.runtime.stop(stop)
                self._cleanup_launch()
            except Exception:
                pass
            finally:
                self.gate.release()
                self.lease.close()
                self.lease = None
