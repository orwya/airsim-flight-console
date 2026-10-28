"""显示号残留文件清理、以及先 SIGTERM 后 SIGKILL 的进程树终止。"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scene_processes import (
    SceneError,
    identity,
    reclaim_stale_display,
    terminate_verified_tree,
)


class StaleDisplayTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.lock_dir = Path(temp.name)
        self.socket_dir = self.lock_dir / ".X11-unix"
        self.socket_dir.mkdir()

    def prepare(self, pid):
        lock = self.lock_dir / ".X100-lock"
        socket_path = self.socket_dir / "X100"
        lock.write_text(f"{pid}\n")
        socket_path.write_text("")
        return lock, socket_path

    def test_dead_pid_leaves_are_removed(self):
        dead = subprocess.Popen(["/bin/true"])
        dead.wait()
        lock, socket_path = self.prepare(dead.pid)
        removed = reclaim_stale_display(":100", self.lock_dir, self.socket_dir)
        self.assertEqual(removed, str(lock))
        self.assertFalse(lock.exists())
        self.assertFalse(socket_path.exists())

    def test_live_pid_is_reported_and_left_alone(self):
        lock, socket_path = self.prepare(os.getpid())
        with self.assertRaises(SceneError):
            reclaim_stale_display(":100", self.lock_dir, self.socket_dir)
        self.assertTrue(lock.exists())
        self.assertTrue(socket_path.exists())

    def test_foreign_owner_is_not_removed(self):
        dead = subprocess.Popen(["/bin/true"])
        dead.wait()
        lock, _ = self.prepare(dead.pid)
        with patch("scene_processes.os.getuid", return_value=os.getuid() + 1):
            with self.assertRaises(SceneError):
                reclaim_stale_display(":100", self.lock_dir, self.socket_dir)
        self.assertTrue(lock.exists())

    def test_corrupt_lock_is_reported(self):
        lock, socket_path = self.prepare(0)
        with self.assertRaises(SceneError):
            reclaim_stale_display(":100", self.lock_dir, self.socket_dir)
        self.assertTrue(lock.exists())
        self.assertTrue(socket_path.exists())

    def test_missing_lock_is_a_no_op(self):
        self.assertIsNone(
            reclaim_stale_display(":100", self.lock_dir, self.socket_dir)
        )


@unittest.skipUnless(sys.platform.startswith("linux"), "需要 /proc 的进程树语义")
class TerminateTreeTests(unittest.TestCase):
    def test_sigterm_alone_stops_the_process(self):
        process = subprocess.Popen(["/bin/sleep", "30"])
        self.addCleanup(process.kill)
        stopped = terminate_verified_tree(process.pid, identity(process.pid), 5, 0.05)
        self.assertIn(process.pid, stopped)
        self.assertIsNotNone(process.wait(timeout=5))

    def test_identity_mismatch_sends_nothing(self):
        process = subprocess.Popen(["/bin/sleep", "30"])
        self.addCleanup(process.kill)
        sent = terminate_verified_tree(
            process.pid, (0, 0, "/nonexistent", []), 0.2, 0.01
        )
        self.assertEqual(sent, [])
        self.assertIsNone(process.poll())

    def test_sigterm_is_escalated_when_ignored(self):
        process = subprocess.Popen(["/bin/sh", "-c", "trap '' TERM; sleep 30"])
        self.addCleanup(process.kill)
        terminate_verified_tree(process.pid, identity(process.pid), 0.3, 0.05)
        self.assertIsNotNone(process.wait(timeout=10))


if __name__ == "__main__":
    unittest.main()