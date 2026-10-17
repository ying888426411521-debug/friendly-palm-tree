import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import desktop  # noqa: E402


def is_rect(r):
    return r is None or (len(r) == 4 and all(isinstance(v, int) for v in r))


class DesktopTest(unittest.TestCase):
    # 在 Windows 构建机上会真正调用系统接口，主要确认不会抛异常、返回值类型正确
    def test_queries_do_not_crash(self):
        self.assertTrue(is_rect(desktop.taskbar_rect()))
        self.assertTrue(is_rect(desktop.tray_rect()))
        self.assertIsInstance(desktop.is_light_theme(), bool)
        self.assertIsInstance(desktop.fullscreen_app_running(), bool)

    @unittest.skipIf(sys.platform == "win32", "仅检查非 Windows 的兜底行为")
    def test_non_windows_fallbacks(self):
        self.assertIsNone(desktop.taskbar_rect())
        self.assertIsNone(desktop.tray_rect())
        self.assertFalse(desktop.is_light_theme())
        self.assertFalse(desktop.fullscreen_app_running())


if __name__ == "__main__":
    unittest.main()
