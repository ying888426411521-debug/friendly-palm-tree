"""和桌面环境打交道的小工具：找任务栏/托盘位置、嵌入任务栏、读系统深浅色、判断是否有全屏程序。

只有 Windows 有真正的实现；其它系统一律返回 None/False，界面会用默认位置和深色主题。
"""

import sys

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    LONG_PTR = ctypes.c_ssize_t

    def _sig(func, restype, *argtypes):
        func.restype = restype
        func.argtypes = argtypes

    _sig(user32.FindWindowW, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
    _sig(user32.FindWindowExW, wintypes.HWND,
         wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
    _sig(user32.GetWindowRect, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    _sig(user32.SetParent, wintypes.HWND, wintypes.HWND, wintypes.HWND)
    _sig(user32.GetParent, wintypes.HWND, wintypes.HWND)
    _sig(user32.IsWindow, wintypes.BOOL, wintypes.HWND)
    _sig(user32.GetWindowLongPtrW, LONG_PTR, wintypes.HWND, ctypes.c_int)
    _sig(user32.SetWindowLongPtrW, LONG_PTR, wintypes.HWND, ctypes.c_int, LONG_PTR)
    _sig(user32.SetWindowPos, wintypes.BOOL, wintypes.HWND, wintypes.HWND,
         ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)

    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _sig(user32.GetDC, wintypes.HDC, wintypes.HWND)
    _sig(user32.ReleaseDC, ctypes.c_int, wintypes.HWND, wintypes.HDC)
    _sig(gdi32.GetPixel, wintypes.DWORD, wintypes.HDC, ctypes.c_int, ctypes.c_int)

GWL_STYLE = -16
WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010


def enable_dpi_awareness():
    """高分屏下字体不发虚；同时让坐标和任务栏一样使用物理像素。

    优先用“每显示器 v2”模式：和 Windows 任务栏（explorer）一致，嵌入任务栏时才不会被系统拒绝。
    """
    if not IS_WINDOWS:
        return
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4，成功返回 TRUE
        if user32.SetProcessDpiAwarenessContext(ctypes.c_ssize_t(-4)):
            return
    except Exception:
        pass
    for level in (2, 1):  # 老系统：每显示器 / 系统级，成功返回 S_OK(0)
        try:
            if ctypes.windll.shcore.SetProcessDpiAwareness(level) == 0:
                return
        except Exception:
            continue


def _window_rect(hwnd):
    rect = wintypes.RECT()
    if hwnd and user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return rect.left, rect.top, rect.right, rect.bottom
    return None


def taskbar_hwnd():
    if not IS_WINDOWS:
        return None
    try:
        return user32.FindWindowW("Shell_TrayWnd", None) or None
    except Exception:
        return None


def taskbar_rect():
    """主屏任务栏的 (left, top, right, bottom)，找不到返回 None。"""
    hwnd = taskbar_hwnd()
    return _window_rect(hwnd) if hwnd else None


def tray_rect():
    """任务栏右侧通知区域（托盘图标 + 时钟）的位置，找不到或看起来不对时返回 None。"""
    bar = taskbar_hwnd()
    if not bar:
        return None
    try:
        rect = _window_rect(user32.FindWindowExW(bar, None, "TrayNotifyWnd", None))
    except Exception:
        return None
    bar_rect = _window_rect(bar)
    if not rect or not bar_rect:
        return None
    width = rect[2] - rect[0]
    # 新版 Windows 11 里这个窗口可能是空的或者铺满整个任务栏，这种情况不可信
    if width <= 0 or width > (bar_rect[2] - bar_rect[0]) / 2 or rect[0] < bar_rect[0]:
        return None
    return rect


def embed_in_taskbar(hwnd):
    """把窗口变成任务栏的子窗口（和 TrafficMonitor 一样的做法）。

    嵌入后窗口就是任务栏的一部分：点任务栏不会被盖住，全屏时随任务栏一起隐藏。
    成功返回任务栏句柄，失败返回 None（调用方退回到普通置顶窗口）。
    """
    bar = taskbar_hwnd()
    if not bar or not hwnd:
        return None
    try:
        try:
            user32.SetThreadDpiHostingBehavior(1)  # DPI_HOSTING_BEHAVIOR_MIXED
        except AttributeError:
            pass
        style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, (style & ~WS_POPUP) | WS_CHILD)
        user32.SetParent(hwnd, bar)
        if user32.GetParent(hwnd) != bar:
            user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style)
            return None
        bring_to_top(hwnd)
        return bar
    except Exception:
        return None


def bring_to_top(hwnd):
    """嵌入后保持在任务栏其它内容（如 Windows 11 的任务栏按钮层）之上。"""
    if IS_WINDOWS and hwnd:
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def screen_colors(points):
    """读取屏幕上若干点的颜色 [(r, g, b), ...]，读不到的点跳过。"""
    if not IS_WINDOWS:
        return []
    colors = []
    hdc = user32.GetDC(None)
    try:
        for x, y in points:
            c = gdi32.GetPixel(hdc, x, y)
            if c != 0xFFFFFFFF:  # CLR_INVALID
                colors.append((c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF))
    finally:
        user32.ReleaseDC(None, hdc)
    return colors


def is_window(hwnd):
    return bool(IS_WINDOWS and hwnd and user32.IsWindow(hwnd))


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
