"""Fixed-loopback Planner HTTP client. No retries for task or stop requests."""
import http.client
import json
import socket
import time
from urllib.parse import urlencode

from config import LOOPBACK

STATES = frozenset(("connecting", "idle", "running", "stopping"))


class PlannerError(Exception):
    def __init__(self, message, status=502, code="planner_unavailable"):
        super().__init__(message)
        self.status = status
        self.body = {"error": code, "message": message}


class PlannerService:
    def __init__(self, settings):
        self.settings = settings
        self.enabled = settings.planner_port is not None

    def request(self, method, path, body=None, timeout=None):
        if not self.enabled:
            raise PlannerError("Planner 未配置", 503, "planner_disabled")
        # Caller supplies only one of the four fixed paths.
        limit = min(self.settings.planner_timeout, timeout) if timeout is not None else self.settings.planner_timeout
        connection = http.client.HTTPConnection(LOOPBACK, self.settings.planner_port, timeout=limit)
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise PlannerError("Planner 响应过大", code="planner_protocol")
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeError) as error:
                raise PlannerError("Planner 返回无效 JSON；写操作结果未确认", code="planner_protocol") from error
            if not isinstance(result, dict):
                raise PlannerError("Planner 返回无效对象；写操作结果未确认", code="planner_protocol")
            return result, response.status
        except (socket.timeout, TimeoutError) as error:
            raise PlannerError("Planner 请求超时；写操作结果未确认，不会自动重发", 504, "planner_timeout") from error
        except (OSError, http.client.HTTPException) as error:
            raise PlannerError("Planner HTTP 连接失败；写操作结果未确认，不会自动重发", code="planner_unavailable") from error
        finally:
            connection.close()

    def status(self, timeout=None):
        body, status = self.request("GET", "/api/status", timeout=timeout)
        if status != 200 or body.get("state") not in tuple(STATES):
            raise PlannerError("无法确认 Planner 状态", code="planner_protocol")
        return body

    def submit(self, payload):
        return self.request("POST", "/api/tasks", payload)

    def stop(self, timeout=None):
        return self.request("POST", "/api/stop", timeout=timeout)

    def logs(self, cursor):
        return self.request("GET", "/api/logs?" + urlencode({"cursor": cursor}))

    def wait_idle(self, cancel, stage, *, before_stop=False, has_scene=True):
        """Called while the scene gate excludes new console tasks."""
        deadline = time.monotonic() + self.settings.rpc_ready_timeout

        def remaining():
            cancel()
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise PlannerError("等待 Planner 空闲超时；场景操作中止", 504, "planner_wait_timeout")
            return seconds

        state = self.status(remaining())["state"]
        if before_stop:
            if state == "idle":
                return
            # Without an existing AirSim, connecting cannot finish until launch.
            if state == "connecting" and not has_scene:
                return
            stage("正在停止 Planner 任务，等待空闲")
            body, status = self.stop(remaining())
            if status not in (200, 202) or body.get("state") not in tuple(STATES):
                raise PlannerError("Planner 停止结果未确认；保留旧场景", code="planner_stop_failed")
        elif state in ("running", "stopping"):
            raise PlannerError("新场景连接期间 Planner 非空闲，请检查外部控制端", code="planner_busy")
        while state != "idle":
            cancel()
            time.sleep(min(self.settings.poll_interval, remaining()))
            state = self.status(remaining())["state"]
