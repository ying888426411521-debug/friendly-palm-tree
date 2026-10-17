import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import autostart  # noqa: E402

# 测试用独立的名字，不会碰到真正的 NetWatch 启动项
NAME = "NetWatchUnitTest"


@unittest.skipIf(sys.platform == "darwin", "macOS 上会写入真实的 ~/Library/LaunchAgents")
class AutostartTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name})
        self.env.start()
        autostart.disable(NAME)

    def tearDown(self):
        autostart.disable(NAME)
        self.env.stop()
        self.tmp.cleanup()

    def test_enable_disable_roundtrip(self):
        self.assertFalse(autostart.is_enabled(NAME))
        autostart.enable(NAME)
        self.assertTrue(autostart.is_enabled(NAME))
        self.assertIn("netwatch.py", autostart._read(NAME))
        autostart.disable(NAME)
        self.assertFalse(autostart.is_enabled(NAME))
        autostart.disable(NAME)  # 重复关闭不报错

    def test_refresh_updates_moved_program(self):
        autostart._write(NAME, "old-path")
        autostart.refresh(NAME)
        self.assertEqual(autostart._read(NAME), autostart._entry(NAME))

    def test_refresh_does_not_enable(self):
        autostart.refresh(NAME)
        self.assertFalse(autostart.is_enabled(NAME))

    def test_frozen_exe_launches_itself(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", "C:\\Apps\\NetWatch.exe"):
            self.assertEqual(autostart.launch_command(), ["C:\\Apps\\NetWatch.exe"])


if __name__ == "__main__":
    unittest.main()
