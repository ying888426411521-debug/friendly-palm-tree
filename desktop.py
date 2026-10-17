"""和桌面环境打交道的小工具：找任务栏/托盘位置、读系统深浅色、判断是否有全屏程序。

只有 Windows 有真正的实现；其它系统一律返回 None/False，界面会用默认位置和深色主题。
"""

import sys

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes


def _window_rect(hwnd):
    rect = wintypes.RECT()
    if hwnd and ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return rect.left, rect.top, rect.right, rect.bottom
    return None


def taskbar_rect():
    """主屏任务栏的 (left, top, right, bottom)，找不到返回 None。"""
    if not IS_WINDOWS:
        return None
    try:
        return _window_rect(ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None))
    except Exception:
        return None


def tray_rect():
    """任务栏右侧通知区域（托盘图标 + 时钟）的位置，找不到或看起来不对时返回 None。"""
    if not IS_WINDOWS:
        return None
    try:
        user32 = ctypes.windll.user32
        bar = user32.FindWindowW("Shell_TrayWnd", None)
        rect = _window_rect(user32.FindWindowExW(bar, None, "TrayNotifyWnd", None))
    except Exception:
        return None
    bar_rect = taskbar_rect()
    if not rect or not bar_rect:
        return None
    width = rect[2] - rect[0]
    # 新版 Windows 11 里这个窗口可能是空的或者铺满整个任务栏，这种情况不可信
    if width <= 0 or width > (bar_rect[2] - bar_rect[0]) / 2 or rect[0] < bar_rect[0]:
        return None
    return rect


def is_light_theme():
    """Windows 任务栏是否为浅色（设置 → 个性化 → 颜色 → 选择 Windows 模式）。"""
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "SystemUsesLightTheme")[0] == 1
    except OSError:
        return False


def fullscreen_app_running():
    """正在全屏看视频 / 玩游戏 / 放 PPT 时返回 True，小窗应暂时隐藏。"""
    if not IS_WINDOWS:
        return False
    try:
        state = ctypes.c_int()
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return False
        # 2 = 有全屏程序，3 = Direct3D 全屏，4 = 演示模式
        return state.value in (2, 3, 4)
    except Exception:
        return False
