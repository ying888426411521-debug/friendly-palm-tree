"""开机自启动（只对当前用户生效，不需要管理员权限）。

- Windows：注册表 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
- macOS：~/Library/LaunchAgents/com.netwatch.plist
- Linux：~/.config/autostart/netwatch.desktop
"""

import os
import plistlib
import sys

import monitor

APP_NAME = "NetWatch"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command() -> list:
    """开机时要执行的命令：打包后是 exe 本身，源码运行时是 pythonw + netwatch.py。"""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    exe = sys.executable
    if sys.platform == "win32":
        pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(pythonw):
            exe = pythonw  # 不弹出黑色命令行窗口
    return [exe, os.path.join(monitor.app_dir(), "netwatch.py")]


def _quoted(args) -> str:
    return " ".join(f'"{a}"' for a in args)


# ---------------------------------------------------------------- Windows

def _win_read(name):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _win_write(name, value):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _win_remove(name):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------- macOS / Linux

def _entry_path(name):
    if sys.platform == "darwin":
        return os.path.expanduser(f"~/Library/LaunchAgents/com.{name.lower()}.plist")
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(config_home, "autostart", f"{name.lower()}.desktop")


def _file_read(name):
    try:
        with open(_entry_path(name), "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _file_write(name, value):
    path = _entry_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(value)


def _file_remove(name):
    try:
        os.remove(_entry_path(name))
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------- 对外接口

def _entry(name) -> str:
    cmd = launch_command()
    if sys.platform == "win32":
        return _quoted(cmd)
    if sys.platform == "darwin":
        plist = {"Label": f"com.{name.lower()}", "ProgramArguments": cmd, "RunAtLoad": True}
        return plistlib.dumps(plist).decode("utf-8")
    return ("[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={name}\n"
            f"Exec={_quoted(cmd)}\n"
            "X-GNOME-Autostart-enabled=true\n")


def _read(name):
    return _win_read(name) if sys.platform == "win32" else _file_read(name)


def _write(name, value):
    (_win_write if sys.platform == "win32" else _file_write)(name, value)


def is_enabled(name: str = APP_NAME) -> bool:
    return _read(name) is not None


def enable(name: str = APP_NAME) -> None:
    _write(name, _entry(name))


def disable(name: str = APP_NAME) -> None:
    (_win_remove if sys.platform == "win32" else _file_remove)(name)


def refresh(name: str = APP_NAME) -> None:
    """已开启自启动但程序被挪了位置时，把启动项更新为当前路径。"""
    current = _read(name)
    if current is not None and current != _entry(name):
        _write(name, _entry(name))
