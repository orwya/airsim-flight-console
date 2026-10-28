"""Linux 进程检查：端口持有者、进程归属、受管停止与窗口确认。"""

from pathlib import Path
import os
import re
import signal
import shlex
import socket
import subprocess
import time

from config import LOOPBACK


class SceneError(Exception):
    pass


def port_alive(port):
    with socket.socket() as connection:
        connection.settimeout(0.3)
        return connection.connect_ex((LOOPBACK, port)) == 0


def listener_pids(port):
    result = subprocess.run(
        ["ss", "-H", "-ltnp", f"sport = :{port}"],
        capture_output=True,
        text=True,
        timeout=3,
        check=True,
    )
    pids = set(map(int, re.findall(r"pid=(\d+)", result.stdout)))
    if port_alive(port) and not pids:
        raise SceneError(f"端口 {port} 已占用，无法确认监听进程归属")
    return pids


def identity(pid):
    root = Path("/proc") / str(pid)
    try:
        fields = (root / "stat").read_text().rsplit(")", 1)[1].split()
        return (
            int(fields[19]),
            root.stat().st_uid,
            os.readlink(root / "exe"),
            (root / "cmdline")
            .read_bytes()
            .decode(errors="replace")
            .rstrip("\0")
            .split("\0"),
        )
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None


def descendants(pid):
    result = []
    seen = set()

    def walk(parent):
        if parent in seen:
            return
        seen.add(parent)
        tasks = Path(f"/proc/{parent}/task")
        try:
            children = set()
            for task in tasks.iterdir():
                try:
                    children.update(map(int, (task / "children").read_text().split()))
                except (OSError, ValueError):
                    pass
            for child in children:
                walk(child)
            item = identity(parent)
            if item:
                result.append((parent, item))
        except OSError:
            pass

    walk(pid)
    return result


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_verified_tree(pid, expected, wait, interval):
    """先发 SIGTERM 让进程自己收尾，到点还活着才 SIGKILL。

    Xvfb 只在正常退出时删除 /tmp/.X{n}-lock 和 /tmp/.X11-unix/X{n}。
    直接 SIGKILL 会留下这两个文件，下次在同一显示号上无法启动。
    """
    if identity(pid) != expected:
        return []
    captured = [
        (target, before)
        for target, before in descendants(pid)
        if before[1] == os.getuid()
    ]
    for target, before in captured:
        if identity(target) == before:
            try:
                os.kill(target, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + wait
    alive = captured
    while alive and time.monotonic() < deadline:
        time.sleep(interval)
        alive = [
            (target, before) for target, before in alive if identity(target) == before
        ]
    for target, before in alive:
        if identity(target) == before:
            try:
                os.kill(target, signal.SIGKILL)
            except ProcessLookupError:
                pass
    return [target for target, _ in captured]


X11_SOCKET_DIR = Path("/tmp/.X11-unix")


def display_files(display, lock_dir=Path("/tmp"), socket_dir=X11_SOCKET_DIR):
    number = display[1:]
    return lock_dir / f".X{number}-lock", socket_dir / f"X{number}"


def reclaim_stale_display(display, lock_dir=Path("/tmp"), socket_dir=X11_SOCKET_DIR):
    """清理已被强杀的 Xvfb 留下的锁文件与 socket。

    只在锁文件里的 PID 已不存在、且文件属于当前用户时删除。
    进程还活着就报错，不动正在使用的显示号。
    """
    lock, socket_path = display_files(display, lock_dir, socket_dir)
    if not lock.exists():
        return None
    try:
        pid = int(lock.read_text().strip() or "0")
    except (OSError, ValueError) as error:
        raise SceneError(f"无法读取显示锁文件 {lock}：{error}") from error
    if pid <= 0:
        raise SceneError(f"无法识别显示锁文件 {lock} 中的 PID：{pid}")
    if pid_alive(pid):
        raise SceneError(
            f"显示 {display} 仍被 PID {pid} 占用；先停止它，或改用其他 --display"
        )
    owner = lock.stat().st_uid
    if owner != os.getuid():
        raise SceneError(f"显示锁文件 {lock} 属于 UID {owner}，需要该用户或 root 处理")
    lock.unlink()
    socket_path.unlink(missing_ok=True)
    return str(lock)


class ProcessRuntime:
    def __init__(self, settings):
        self.settings = settings
        self.root = settings.root
        self.path = self.root / ".runtime"
        self.env = dict(os.environ)
        for name in ("logs", "sockets", "sessions"):
            (self.path / name).mkdir(parents=True, exist_ok=True)

    def owned_scene(self, pid):
        from scene_launcher import SCENES

        info = identity(pid)
        if not info or info[1] != os.getuid():
            return None
        exe, argv = info[2], info[3]
        # Xpra uses setproctitle, replacing argv with a single display string.
        if len(argv) == 1 and argv[0].startswith("/usr/bin/xpra "):
            try:
                argv = shlex.split(argv[0])
            except ValueError:
                return None
        marker = f"-settings={self.settings.airsim_settings}"
        for key, scene in SCENES.items():
            if exe == scene.executable and marker in argv:
                return key
        if (
            exe == os.path.realpath("/usr/bin/python3")
            and "/usr/bin/xpra" in argv
            and "start" in argv
            and self.settings.display in argv
            and f"--socket-dir={self.path / 'sockets'}" in argv
        ):
            for key, scene in SCENES.items():
                if any(
                    arg.startswith("--start-child=") and scene.executable in arg
                    for arg in argv
                ):
                    return key
        return None

    def holders(self):
        holders = {}
        for port in (self.settings.xpra_port, self.settings.rpc_port):
            for pid in listener_pids(port):
                key = self.owned_scene(pid)
                if not key:
                    info = identity(pid)
                    command = " ".join(info[3])[:350] if info else "exited"
                    raise SceneError(
                        f"端口 {port} 属于未受管进程 PID {pid}: {command}；未执行清理"
                    )
                holders[pid] = (key, identity(pid))
        return holders

    def stop(self, stop_args):
        holders = self.holders()  # Validate every holder before taking any action.
        killed = []
        actions = [
            {"pid": pid, "scene": key, "action": "xpra_stop"}
            for pid, (key, _) in holders.items()
        ]
        if not holders:
            return killed
        try:
            subprocess.run(
                stop_args,
                env=self.env,
                cwd=self.root,
                capture_output=True,
                timeout=self.settings.port_release_wait + 2,
            )
        except subprocess.TimeoutExpired:
            pass
        deadline = time.monotonic() + self.settings.port_release_wait
        while time.monotonic() < deadline:
            if not any(
                port_alive(p) for p in (self.settings.xpra_port, self.settings.rpc_port)
            ):
                return actions + [{"pid": pid, "action": "killed"} for pid in killed]
            time.sleep(self.settings.poll_interval)
        # Re-read and revalidate ownership, including PID identity, before a forced kill.
        for pid, (_, before) in self.holders().items():
            killed.extend(
                terminate_verified_tree(
                    pid, before, self.settings.kill_wait, self.settings.poll_interval
                )
            )
        deadline = time.monotonic() + self.settings.kill_wait
        while time.monotonic() < deadline:
            if not any(
                port_alive(p) for p in (self.settings.xpra_port, self.settings.rpc_port)
            ):
                return actions + [{"pid": pid, "action": "killed"} for pid in killed]
            time.sleep(self.settings.poll_interval)
        self.holders()
        raise SceneError("停止超时，画面或 RPC 端口仍被占用")

    def window_ready(self, scene):
        result = subprocess.run(
            [
                "/usr/bin/xpra",
                "info",
                "socket://"
                + str(self.path / "sessions" / self.settings.display[1:] / "socket"),
                f"--socket-dir={self.path / 'sockets'}",
                f"--socket-dirs={self.path / 'sockets'}",
            ],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=4,
        )
        for match in re.finditer(
            r"^windows\.([^.]+)\.pid=(\d+)$", result.stdout, re.MULTILINE
        ):
            wid, pid = match.group(1), int(match.group(2))
            if self.owned_scene(pid) == scene:
                # Seamless xpra leaves windows unmapped until a client attaches.
                # Vulkan can then block the game thread, including weather RPC.
                for command in ("map", "focus"):
                    response = subprocess.run(
                        [
                            "/usr/bin/xpra",
                            "control",
                            "socket://"
                            + str(
                                self.path
                                / "sessions"
                                / self.settings.display[1:]
                                / "socket"
                            ),
                            f"--socket-dir={self.path / 'sockets'}",
                            f"--socket-dirs={self.path / 'sockets'}",
                            command,
                            wid,
                        ],
                        env=self.env,
                        capture_output=True,
                        text=True,
                        timeout=4,
                    )
                    if response.returncode:
                        return False
                return True
        return False
