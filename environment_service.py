"""Serialized scene switching: one operation at a time, ownership checked before any kill."""

from pathlib import Path
import fcntl
import os
import subprocess
import threading
import time
import uuid

from config import LOOPBACK, Settings
from planner_service import PlannerError
from scene_launcher import SCENES, get_commands
from scene_processes import (
    ProcessRuntime,
    SceneError,
    identity,
    kill_verified_tree,
    port_alive,
)
from weather_service import WeatherUnavailable, AirSimWeatherService


class SceneBusy(SceneError):
    pass


class EnvironmentService:
    def __init__(self, settings: Settings, weather: AirSimWeatherService):
        self.settings = settings
        self.planner = None
        self.weather = weather
        self.startup_weather = AirSimWeatherService(LOOPBACK, weather.port, 30)
        self.root = settings.root
        self.runtime = None
        self.gate = threading.Lock()
        self.state_lock = threading.Lock()
        self.cancel = threading.Event()
        self.worker = None
        self.lease = None
        self.process = None
        self.process_identity = None
        self.state = dict(
            manager_id=str(uuid.uuid4()),
            revision=0,
            phase="idle",
            scene=None,
            target_scene=None,
            previous_scene=None,
            instance_id=None,
            operation_id=None,
            weather=None,
            error=None,
            cleanup=[],
            stage="请选择一个场景启动",
            completed_operation_id=None,
        )

    def _set(self, **values):
        with self.state_lock:
            self.state.update(values)
            self.state["revision"] += 1

    def snapshot(self, refresh=True):
        with self.state_lock:
            state = dict(self.state)
        xpra = port_alive(self.settings.xpra_port)
        rpc = port_alive(self.settings.rpc_port)
        if refresh and state["phase"] == "ready" and not (xpra and rpc):
            # Do not race a switch that started during the probe.
            with self.state_lock:
                if (
                    self.state["phase"] == "ready"
                    and self.state["instance_id"] == state["instance_id"]
                ):
                    self.state["revision"] += 1
                    self.state.update(
                        phase="error",
                        scene=None,
                        weather=None,
                        error="场景连接已断开，请重试场景或清理",
                        stage="场景异常退出",
                    )
                state = dict(self.state)
        return dict(state, port_xpra=xpra, port_rpc=rpc)

    def _prepare(self):
        if self.runtime is None:
            self.runtime = ProcessRuntime(self.settings)
        if self.lease is None:
            stream = open(self.runtime.path / "manager.lock", "a+")
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                stream.close()
                raise SceneError("另一个场景管理器正在运行")
            self.lease = stream

    def submit(self, scene_id=None, cleanup=False):
        if not cleanup and (not isinstance(scene_id, str) or scene_id not in SCENES):
            raise ValueError("未知场景")
        acquired = (self.gate.acquire(timeout=2 * self.settings.planner_timeout + 1)
                    if self.planner else self.gate.acquire(blocking=False))
        if not acquired:
            raise SceneBusy("场景或天气正在处理中，请稍后重试")
        try:
            self._prepare()
            current = self.snapshot()
            if (
                not cleanup
                and current["phase"] == "ready"
                and current["scene"] == scene_id
            ):
                holders = self.runtime.holders()
                if not holders or any(key != scene_id for key, _ in holders.values()):
                    raise SceneError("当前监听进程与所选场景不匹配")
                self.gate.release()
                return current, False
            operation = str(uuid.uuid4())
            self._set(
                phase="stopping",
                target_scene=scene_id,
                previous_scene=current["scene"],
                operation_id=operation,
                error=None,
                cleanup=[],
                stage="正在检查和停止旧场景",
            )
            self.worker = threading.Thread(
                target=self._run,
                args=(scene_id, cleanup),
                daemon=True,
                name="scene-switch",
            )
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
                raise SceneError(
                    f"启动进程已退出（{self.process.returncode}），请检查 .runtime/logs/launcher.log"
                )
            try:
                if condition():
                    return
            except (WeatherUnavailable, subprocess.TimeoutExpired) as error:
                last_error = error
            time.sleep(self.settings.poll_interval)
        raise SceneError(
            f"{description}超时" + (f"：{last_error}" if last_error else "")
        )

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

    # --- one switch: stop whatever runs, launch the target, wait until usable ----

    def _run(self, scene_id, cleanup):
        launched = False
        try:
            if not cleanup:
                self._check_scene_files(scene_id)
            if self.planner:
                self.runtime.holders()  # Reject foreign listeners before affecting Planner.
                self._set(stage="正在确认 Planner 已停止")
                has_scene = any(port_alive(p) for p in (self.settings.xpra_port, self.settings.rpc_port))
                self.planner.wait_idle(self._check_cancel,
                    lambda text: self._set(stage=text), before_stop=True, has_scene=has_scene)
            self._check_cancel()
            self._stop_current(scene_id)
            if cleanup:
                self._set(
                    phase="idle",
                    target_scene=None,
                    stage="清理完成",
                    error=None,
                    completed_operation_id=self.state["operation_id"],
                )
                return
            self._check_cancel()
            launched = self._launch(scene_id)
            self._wait_ready(scene_id)
            self._set(phase="initializing", stage="正在初始化晴朗天气")
            self.startup_weather.apply("clear")
            if self.planner:
                self._set(stage="晴朗已初始化，等待 Planner 连接准备完成")
                self.planner.wait_idle(self._check_cancel, lambda text: self._set(stage=text))
            self._set(
                phase="ready",
                scene=scene_id,
                target_scene=None,
                weather="clear",
                error=None,
                stage="场景已就绪",
                completed_operation_id=self.state["operation_id"],
            )
        except Exception as error:
            # Clean only the launch owned by this operation, never an unknown port holder.
            if launched:
                self._cleanup_launch()
            self._set(
                phase="error",
                target_scene=None,
                error=str(error)[:1000],
                stage="操作失败，可重试或清理",
                completed_operation_id=self.state["operation_id"],
            )
        finally:
            self.gate.release()

    @staticmethod
    def _check_scene_files(scene_id):
        scene = SCENES[scene_id]
        if not Path(scene.executable).is_file() or not os.access(
            scene.executable, os.X_OK
        ):
            raise SceneError(f"场景程序缺失或无执行权限：{scene.name}")

    def _stop_current(self, stop_scene):
        _, stop = get_commands(
            stop_scene or next(iter(SCENES)), self.settings, self.runtime.path
        )
        actions = self.runtime.stop(stop)
        self._set(cleanup=actions)
        self._cleanup_launch()
        self._set(scene=None, instance_id=None, weather=None)

    def _launch(self, scene_id):
        instance = str(uuid.uuid4())
        self._set(
            phase="starting",
            instance_id=instance,
            stage=f"正在启动 {SCENES[scene_id].name}",
        )
        start, _ = get_commands(scene_id, self.settings, self.runtime.path)
        with open(self.runtime.path / "logs" / "launcher.log", "a") as log:
            log.write(f"\n--- {time.time()} {scene_id} {instance} ---\n")
            log.flush()
            self.process = subprocess.Popen(
                start,
                env=self.runtime.env,
                cwd=self.root,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.process_identity = identity(self.process.pid)
        return True

    def _wait_ready(self, scene_id):
        self._wait(
            lambda: port_alive(self.settings.xpra_port),
            self.settings.xpra_ready_timeout,
            "画面端口启动",
        )
        self._set(stage="等待目标场景 RPC 与窗口")
        self._wait(
            lambda: self._scene_responding(scene_id),
            self.settings.rpc_ready_timeout,
            "目标场景 RPC 或窗口就绪",
        )

    def _scene_responding(self, scene_id):
        if not port_alive(self.settings.rpc_port):
            return False
        holders = self.runtime.holders()
        if not holders or any(key != scene_id for key, _ in holders.values()):
            raise SceneError("监听进程与目标场景不匹配")
        self.weather.probe()
        return self.runtime.window_ready(scene_id)

    def submit_task(self, payload):
        if self.planner is None:
            raise PlannerError("Planner 未配置", 503, "planner_disabled")
        if not self.gate.acquire(timeout=2 * self.settings.planner_timeout + 1):
            raise SceneBusy("场景或天气正在处理中，请稍后重试")
        try:
            if self.snapshot()["phase"] != "ready":
                raise SceneBusy("场景尚未就绪")
            body, status = self.planner.request("GET", "/api/status")
            if status != 200:
                return body, status
            state = body.get("state")
            if state not in ("idle", "running", "stopping", "connecting"):
                raise PlannerError("无法确认 Planner 状态", code="planner_protocol")
            if state != "idle":
                return {"detail": {"state": state, "message": "Planner is " + state + "."}}, 503 if state == "connecting" else 409
            return self.planner.submit(payload)
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
                if not holders or any(
                    key != state["scene"] for key, _ in holders.values()
                ):
                    raise SceneError("场景监听归属已变化")
            except SceneError as error:
                self._set(
                    phase="error",
                    scene=None,
                    weather=None,
                    error=str(error),
                    stage="场景归属异常",
                )
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
            self.worker.join(
                timeout=self.settings.port_release_wait + self.settings.kill_wait + 8
            )
        if self.lease is not None and self.gate.acquire(timeout=3):
            try:
                _, stop = get_commands(
                    next(iter(SCENES)), self.settings, self.runtime.path
                )
                self.runtime.stop(stop)
                self._cleanup_launch()
            except Exception:
                pass
            finally:
                self.gate.release()
                self.lease.close()
                self.lease = None
