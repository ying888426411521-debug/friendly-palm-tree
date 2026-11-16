import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import guard  # noqa: E402
import monitor  # noqa: E402

NODE_CLAUDE = ("C:\\Program Files\\nodejs\\node.exe C:\\Users\\me\\AppData\\Roaming\\npm\\"
               "node_modules\\@anthropic-ai\\claude-code\\cli.js")
PROCS = [
    (1, "Claude.exe", "C:\\Users\\me\\AppData\\Local\\AnthropicClaude\\Claude.exe"),
    (2, "claude.exe", "C:\\Users\\me\\.local\\bin\\claude.exe"),
    (3, "node.exe", NODE_CLAUDE),
    (4, "/Applications/Claude.app/Contents/MacOS/Claude", "/Applications/Claude.app/..."),
    (5, "chrome.exe", "chrome.exe https://claude.ai/new"),   # 浏览器里打开 claude.ai 不算
    (6, "claudette.exe", "claudette.exe"),
    (7, "node.exe", "node.exe server.js"),
]


class MatchTest(unittest.TestCase):
    def test_default_rules(self):
        cfg = monitor.DEFAULT_CONFIG
        found = guard.match_processes(PROCS, cfg["kill_process_names"],
                                      cfg["kill_cmdline_patterns"])
        self.assertEqual([p[0] for p in found], [1, 2, 3, 4])

    def test_excluded_pids_are_skipped(self):
        found = guard.match_processes(PROCS, ["claude"], [], exclude={1, 2})
        self.assertEqual([p[0] for p in found], [4])


def snap(level):
    return monitor.Snapshot(level, {monitor.BAD: "VPN 可能断开"}.get(level, "x"))


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.config = dict(monitor.DEFAULT_CONFIG)
        patcher = mock.patch.object(guard, "kill_matching", return_value=["Claude.exe"] * 3)
        self.kill = patcher.start()
        self.addCleanup(patcher.stop)

    def test_needs_two_down_checks_in_a_row(self):
        g = guard.Guard(self.config)
        self.assertEqual(g.on_snapshot(snap(monitor.BAD)), "")
        self.kill.assert_not_called()
        note = g.on_snapshot(snap(monitor.BAD))
        self.assertIn("已结束 Claude.exe（3 个进程）", note)
        self.assertIn("VPN 可能断开", note)

    def test_recovery_resets_the_count(self):
        g = guard.Guard(self.config)
        g.on_snapshot(snap(monitor.BAD))
        g.on_snapshot(snap(monitor.OK))
        g.on_snapshot(snap(monitor.OFFLINE))
        self.kill.assert_not_called()
        g.on_snapshot(snap(monitor.OFFLINE))
        self.kill.assert_called_once()

    def test_yellow_does_not_count(self):
        g = guard.Guard(self.config)
        for _ in range(5):
            g.on_snapshot(snap(monitor.WARN))
        self.kill.assert_not_called()

    def test_keeps_closing_while_still_down(self):
        g = guard.Guard(self.config)
        for _ in range(4):
            g.on_snapshot(snap(monitor.BAD))
        self.assertEqual(self.kill.call_count, 3)

    def test_disabled(self):
        self.config["kill_on_vpn_down"] = False
        g = guard.Guard(self.config)
        for _ in range(3):
            self.assertEqual(g.on_snapshot(snap(monitor.BAD)), "")
        self.kill.assert_not_called()

    def test_nothing_running_gives_no_note(self):
        self.kill.return_value = []
        g = guard.Guard(self.config)
        g.on_snapshot(snap(monitor.BAD))
        self.assertEqual(g.on_snapshot(snap(monitor.BAD)), "")


class RealProcessTest(unittest.TestCase):
    """真的启动几个假进程再结束它们（在 Windows 构建机上也会跑）。

    用不会和真实程序重名的名字：绝不能在测试里匹配 “claude”。
    """

    def spawn(self, name, extra_args=()):
        if sys.platform == "win32":
            src = os.path.join(os.environ["SystemRoot"], "System32", "PING.EXE")
            args, exe = ["-n", "60", "127.0.0.1"], os.path.join(self.dir, name + ".exe")
        else:
            src, args, exe = shutil.which("sleep"), ["60"], os.path.join(self.dir, name)
        shutil.copy(src, exe)
        p = subprocess.Popen([exe, *args, *extra_args] if extra_args else [exe, *args],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (p.poll() is None and p.kill(), p.wait()))
        return p

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def wait_exit(self, p):
        for _ in range(50):
            if p.poll() is not None:
                return True
            time.sleep(0.1)
        return False

    def test_kills_by_name_and_spares_others(self):
        target, survivor = self.spawn("nwtestclaude"), self.spawn("nwsurvivor")
        time.sleep(0.5)
        killed = guard.kill_matching(["nwtestclaude"], [])
        self.assertTrue(self.wait_exit(target), "target process was not terminated")
        self.assertIsNone(survivor.poll(), "a non-matching process was terminated")
        self.assertEqual(len(killed), 1)

    def test_kills_by_command_line(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                              "nwtest-cmdline-marker"])
        self.addCleanup(lambda: (p.poll() is None and p.kill(), p.wait()))
        time.sleep(0.5)
        killed = guard.kill_matching([], ["nwtest-cmdline-marker"])
        self.assertTrue(self.wait_exit(p), "process with matching command line was not terminated")
        self.assertEqual(len(killed), 1)


if __name__ == "__main__":
    unittest.main()
