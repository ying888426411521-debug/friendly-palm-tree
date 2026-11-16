"""VPN 断开时自动结束指定程序（默认是 Claude 桌面版和 Claude Code）。

为避免误杀：只有连续若干次检测（默认 2 次，第二次在 10 秒后复查）都判定为
“VPN 断开 / VPN 未生效 / 网络断开”时才动手；VPN 断开期间如果又打开了，会再次结束。
"""

import os
import signal
import subprocess
import sys
import time
from typing import List, Tuple

import monitor

# 判定为“VPN 断开”的状态（红色）；黄色（个别网站不通、网速慢）不算
DOWN_LEVELS = (monitor.BAD, monitor.OFFLINE)

Process = Tuple[int, str, str]  # (pid, 进程名, 完整命令行)


def list_processes() -> List[Process]:
    if sys.platform == "win32":
        script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                  "Get-CimInstance Win32_Process | ForEach-Object { "
                  "\"$($_.ProcessId)`t$($_.Name)`t$($_.CommandLine)\" }")
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, timeout=30,
                             creationflags=0x08000000).stdout  # CREATE_NO_WINDOW
        procs = []
        for line in out.decode("utf-8", "replace").splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3 and parts[0].isdigit():
                procs.append((int(parts[0]), parts[1], parts[2]))
        return procs

    def ps(column):  # 进程名、命令行里都可能有空格，所以分两次取、各自只有一列
        out = subprocess.run(["ps", "-axww", "-o", "pid=", "-o", f"{column}="],
                             capture_output=True, timeout=30).stdout
        rows = {}
        for line in out.decode("utf-8", "replace").splitlines():
            parts = line.strip().split(None, 1)
            if parts and parts[0].isdigit():
                rows[int(parts[0])] = parts[1] if len(parts) > 1 else ""
        return rows

    names, cmds = ps("comm"), ps("args")
    return [(pid, name, cmds.get(pid, "")) for pid, name in names.items()]


def _base_name(name: str) -> str:
    name = os.path.basename(name.replace("\\", "/")).lower()
    return name[:-4] if name.endswith(".exe") else name


def match_processes(procs: List[Process], names, patterns, exclude=()) -> List[Process]:
    """按进程名（不分大小写、可带 .exe）或命令行片段（/ 与 \\ 视为相同）匹配。"""
    wanted = {_base_name(n) for n in names}
    pats = [p.lower().replace("\\", "/") for p in patterns if p]
    found = []
    for pid, name, cmdline in procs:
        if pid in exclude:
            continue
        cmd = cmdline.lower().replace("\\", "/")
        if _base_name(name) in wanted or any(p in cmd for p in pats):
            found.append((pid, name, cmdline))
    return found


def kill_matching(names, patterns) -> List[str]:
    """结束匹配的进程，返回被结束的进程名列表。"""
    me = os.getpid()
    killed = []
    for pid, name, _ in match_processes(list_processes(), names, patterns,
                                        exclude={me, os.getppid()}):
        try:
            os.kill(pid, signal.SIGTERM)  # Windows 上等同于“结束任务”
            killed.append(os.path.basename(name.replace("\\", "/")))
        except OSError:
            pass  # 已经退出了，或没有权限
    return killed


class Guard:
    def __init__(self, config: dict):
        self.config = config  # 与界面共用同一个字典，菜单里切换开关会立刻生效
        self.down_streak = 0

    def on_snapshot(self, snap: "monitor.Snapshot") -> str:
        """每次检测后调用；结束了进程时返回一句说明，否则返回空字符串。"""
        if snap.level not in DOWN_LEVELS:
            self.down_streak = 0
            return ""
        self.down_streak += 1
        cfg = self.config
        if not cfg.get("kill_on_vpn_down", True):
            return ""
        if self.down_streak < max(1, int(cfg.get("kill_after_checks", 2))):
            return ""
        try:
            killed = kill_matching(cfg.get("kill_process_names") or [],
                                   cfg.get("kill_cmdline_patterns") or [])
        except Exception as e:
            return f"{time.strftime('%H:%M')} {snap.summary}，但结束程序失败：{e}"
        if not killed:
            return ""
        names = "、".join(sorted(set(killed)))
        return f"{time.strftime('%H:%M')} {snap.summary}，已结束 {names}（{len(killed)} 个进程）"
