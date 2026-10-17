"""NetWatch —— 屏幕右上角的半透明小窗，实时显示出口 IP 归属地和网络/VPN 是否正常。

操作：
  左键单击      展开 / 收起详情（各网站延迟、ISP 等）
  左键拖动      移动窗口（位置会自动记住）
  右键          菜单：立即刷新、回到右上角、修改配置、退出
"""

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

import monitor

COLORS = {
    monitor.OK: "#3ddc84",
    monitor.WARN: "#ffb020",
    monitor.BAD: "#ff4d4f",
    monitor.OFFLINE: "#8c8c8c",
    monitor.CHECKING: "#4aa8ff",
}
BG = "#1b1d22"
FG = "#e8e8e8"
FG_DIM = "#9aa0a6"
MARGIN = 12  # 距离屏幕边缘的像素


def enable_dpi_awareness():
    # Windows 高分屏下避免字体发虚
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def pick_font(root, size, weight="normal"):
    available = set(tkfont.families(root))
    for name in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
                 "Noto Sans CJK SC", "WenQuanYi Zen Hei", "WenQuanYi Micro Hei"):
        if name in available:
            return tkfont.Font(root=root, family=name, size=size, weight=weight)
    return tkfont.Font(root=root, size=size, weight=weight)


class Checker(threading.Thread):
    """后台线程：定时检测，结果放进队列交给界面线程显示。"""

    def __init__(self, config, results):
        super().__init__(daemon=True)
        self.config = config
        self.results = results
        self.wake = threading.Event()

    def run(self):
        while True:
            try:
                snap = monitor.run_check(self.config)
            except Exception as e:  # 不能让检测线程挂掉
                snap = monitor.Snapshot(monitor.OFFLINE, f"检测出错: {e}")
            self.results.put(snap)
            self.wake.wait(max(5, float(self.config.get("interval_seconds", 30))))
            self.wake.clear()

    def refresh_now(self):
        self.wake.set()


class Widget:
    def __init__(self):
        enable_dpi_awareness()
        self.config = monitor.load_config()
        self.opacity = min(1.0, max(0.2, float(self.config.get("opacity", 0.78))))

        self.root = tk.Tk()
        self.root.title("NetWatch")
        self.root.overrideredirect(True)          # 无边框
        self.root.attributes("-topmost", True)    # 置顶
        self.root.attributes("-alpha", self.opacity)
        self.root.configure(bg=BG)

        self.font = pick_font(self.root, 10, "bold")
        self.font_small = pick_font(self.root, 9)

        self.expanded = False
        self.snapshot = None
        self.last_ip = None
        self.change_note = ""
        self.flash_until = 0.0

        self._build()
        self._place_initial()

        self.results = queue.Queue()
        self.checker = Checker(self.config, self.results)
        self.checker.start()
        self._poll()

    # ------------------------------------------------------------ 界面
    def _build(self):
        self.frame = tk.Frame(self.root, bg=BG, padx=10, pady=6,
                              highlightthickness=1, highlightbackground="#33363d")
        self.frame.pack(fill="both", expand=True)

        top = tk.Frame(self.frame, bg=BG)
        top.pack(fill="x")
        self.dot = tk.Canvas(top, width=10, height=10, bg=BG, highlightthickness=0)
        self.dot_id = self.dot.create_oval(1, 1, 9, 9, fill=COLORS[monitor.CHECKING], outline="")
        self.dot.pack(side="left", padx=(0, 6))
        self.place_label = tk.Label(top, text="检测中…", font=self.font, fg=FG, bg=BG, anchor="w")
        self.place_label.pack(side="left")

        self.status_label = tk.Label(self.frame, text="", font=self.font_small,
                                     fg=FG_DIM, bg=BG, anchor="w", justify="left")
        self.status_label.pack(fill="x", padx=(16, 0))

        self.detail = tk.Frame(self.frame, bg=BG)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="立即刷新", command=self.refresh)
        self.menu.add_command(label="展开/收起详情", command=self.toggle_details)
        self.menu.add_command(label="回到右上角", command=self.reset_position)
        self.menu.add_separator()
        self.menu.add_command(label="修改配置…", command=self.open_config)
        self.menu.add_command(label="退出", command=self.root.destroy)

        self._bind_mouse(self.frame)
        self.root.bind("<Enter>", lambda e: self.root.attributes("-alpha", 1.0))
        self.root.bind("<Leave>", self._on_leave)

    def _on_leave(self, e):
        # 在子控件之间移动也会触发 Leave，只有真正移出窗口才恢复半透明
        if self.root.winfo_containing(e.x_root, e.y_root) is None:
            self.root.attributes("-alpha", self.opacity)

    def _bind_mouse(self, w):
        w.bind("<ButtonPress-1>", self._on_press)
        w.bind("<B1-Motion>", self._on_drag)
        w.bind("<ButtonRelease-1>", self._on_release)
        w.bind("<Button-3>", self._show_menu)
        w.bind("<Button-2>", self._show_menu)  # macOS 触控板右键
        for child in w.winfo_children():
            self._bind_mouse(child)

    def _render(self):
        snap = self.snapshot
        level = snap.level if snap else monitor.CHECKING
        flashing = time.time() < self.flash_until
        color = COLORS[level]
        if flashing and int(time.time() * 2) % 2:
            color = "#ffffff"
        self.dot.itemconfigure(self.dot_id, fill=color)
        self.frame.configure(highlightbackground=COLORS[level] if flashing or level in
                             (monitor.BAD, monitor.OFFLINE) else "#33363d")

        if snap is None:
            self.place_label.configure(text="检测中…")
            self.status_label.configure(text="", fg=FG_DIM)
        else:
            ip = snap.ip
            self.place_label.configure(text=ip.place if ip else "位置未知")
            self.status_label.configure(
                text=f"{ip.ip if ip else '—'}   {snap.summary}",
                fg=FG_DIM if level == monitor.OK else COLORS[level])

        if self.expanded and snap is not None:
            self._fill_details(snap)
            self.detail.pack(fill="x", padx=(16, 0), pady=(4, 0))
        else:
            self.detail.pack_forget()
        self._fit()

    def _fill_details(self, snap):
        for child in self.detail.winfo_children():
            child.destroy()

        def cell(text, row, col, fg=FG_DIM, colspan=1):
            tk.Label(self.detail, text=text, font=self.font_small, fg=fg, bg=BG,
                     anchor="w").grid(row=row, column=col, columnspan=colspan,
                                      sticky="w", padx=(0, 8))

        rows = list(snap.sites) + ([snap.domestic] if snap.domestic else [])
        for i, s in enumerate(rows):
            color = COLORS[monitor.OK] if s.ok else COLORS[monitor.BAD]
            cell("✓" if s.ok else "✗", i, 0, color)
            cell(s.name + ("（国内）" if s is snap.domestic else ""), i, 1)
            cell(f"{s.latency_ms}ms" if s.ok else s.error, i, 2, FG if s.ok else color)

        extra = []
        if snap.ip and snap.ip.isp:
            extra.append(f"ISP: {snap.ip.isp}")
        if self.change_note:
            extra.append(self.change_note)
        extra.append("更新于 " + time.strftime("%H:%M:%S", time.localtime(snap.checked_at))
                     + (f" · {snap.ip.source}" if snap.ip else ""))
        for j, text in enumerate(extra):
            cell(text, len(rows) + j, 0, colspan=3)
        self._bind_mouse(self.detail)

    # ------------------------------------------------------------ 位置
    def _place_initial(self):
        pos = self.config.get("position")
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        if isinstance(pos, list) and len(pos) == 2 and 0 < pos[0] <= sw and 0 <= pos[1] < sh:
            self.right, self.top = int(pos[0]), int(pos[1])
        else:
            self.right, self.top = sw - MARGIN, MARGIN
        self._fit()

    def _fit(self):
        # 窗口以右边缘为锚点：展开/内容变长时向左长，不会跑出屏幕
        self.root.update_idletasks()
        w = self.frame.winfo_reqwidth()
        h = self.frame.winfo_reqheight()
        self.root.geometry(f"{w}x{h}+{max(0, self.right - w)}+{self.top}")

    def reset_position(self):
        self.right, self.top = self.root.winfo_screenwidth() - MARGIN, MARGIN
        self.config["position"] = None
        monitor.save_config(self.config)
        self._fit()

    def _on_press(self, e):
        self._press = (e.x_root, e.y_root, self.right, self.top)
        self._moved = False

    def _on_drag(self, e):
        x0, y0, r0, t0 = self._press
        dx, dy = e.x_root - x0, e.y_root - y0
        if abs(dx) + abs(dy) > 3:
            self._moved = True
        if self._moved:
            self.right, self.top = r0 + dx, max(0, t0 + dy)
            self._fit()

    def _on_release(self, e):
        if self._moved:
            self.config["position"] = [self.right, self.top]
            monitor.save_config(self.config)
        else:
            self.toggle_details()

    # ------------------------------------------------------------ 操作
    def toggle_details(self):
        self.expanded = not self.expanded
        self._render()

    def refresh(self):
        self.dot.itemconfigure(self.dot_id, fill=COLORS[monitor.CHECKING])  # 蓝点表示检测中
        self.checker.refresh_now()

    def open_config(self):
        path = monitor.CONFIG_PATH
        if not os.path.exists(path):
            monitor.save_config(self.config)
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass

    def _show_menu(self, e):
        self.menu.tk_popup(e.x_root, e.y_root)

    # ------------------------------------------------------------ 结果处理
    def _poll(self):
        try:
            while True:
                self._on_snapshot(self.results.get_nowait())
        except queue.Empty:
            pass
        if time.time() < self.flash_until + 1:
            self._render()
        self.root.attributes("-topmost", True)
        self.root.after(500, self._poll)

    def _on_snapshot(self, snap):
        ip = snap.ip
        if ip and self.last_ip and ip.ip != self.last_ip.ip:
            # IP 变了（换节点 / VPN 掉线回落到直连），闪烁提醒几秒
            self.change_note = (f"IP 变化 {time.strftime('%H:%M')}: "
                                f"{self.last_ip.place} → {ip.place}")
            self.flash_until = time.time() + 6
        if ip:
            self.last_ip = ip
        self.snapshot = snap
        self._render()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    Widget().run()
