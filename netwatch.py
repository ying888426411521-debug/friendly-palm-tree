"""NetWatch —— 嵌在任务栏托盘旁边的小部件，实时显示出口 IP 归属地和网络/VPN 是否正常。

  展开：‹ ● 新加坡        折叠：› ●
          正常 448ms
  圆点颜色：绿 = 正常，黄 = 部分网站不通/网速慢，红 = VPN 断开或网络断开

操作：
  点 ‹ / ›      折叠 / 展开
  单击圆点/文字  弹出详情（IP、各网站延迟、ISP 等），再点一次关闭
  左键拖动      移动位置（会自动记住；嵌入任务栏时只能左右移动）
  右键          菜单：立即刷新、停靠到任务栏、嵌入任务栏、开机自启动、修改配置、退出
"""

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

import autostart
import desktop
import monitor

COLORS = {
    monitor.OK: "#22c55e",
    monitor.WARN: "#f5a524",
    monitor.BAD: "#ef4444",
    monitor.OFFLINE: "#ef4444",
    monitor.CHECKING: "#9ca3af",
}
THEMES = {
    "dark": {"bg": "#202020", "hover": "#303030", "fg": "#ffffff", "dim": "#c4c4c4"},
    "light": {"bg": "#eef1f5", "hover": "#e0e5eb", "fg": "#1b1b1b", "dim": "#5c6370"},
}


def pick_font(root, size, weight="normal"):
    available = set(tkfont.families(root))
    for name in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
                 "Noto Sans CJK SC", "WenQuanYi Zen Hei", "WenQuanYi Micro Hei"):
        if name in available:
            return tkfont.Font(root=root, family=name, size=size, weight=weight)
    return tkfont.Font(root=root, size=size, weight=weight)


def full_place(ip):
    parts = []
    for p in (ip.country, ip.region, ip.city):
        if p and p not in parts:
            parts.append(p)
    return " · ".join(parts) or "未知"


class Checker(threading.Thread):
    """后台线程：定时检测，结果放进队列交给界面线程显示。"""

    def __init__(self, config, results):
        super().__init__(daemon=True)
        self.config = config
        self.results = results
        self.wake = threading.Event()
        self.stopped = False

    def run(self):
        while not self.stopped:
            try:
                snap = monitor.run_check(self.config)
            except Exception as e:  # 不能让检测线程挂掉
                snap = monitor.Snapshot(monitor.OFFLINE, f"检测出错: {e}")
            if self.stopped:
                break
            self.results.put(snap)
            self.wake.wait(max(5, float(self.config.get("interval_seconds", 30))))
            self.wake.clear()

    def refresh_now(self):
        self.wake.set()

    def stop(self):
        self.stopped = True
        self.wake.set()


class Widget:
    def __init__(self):
        desktop.enable_dpi_awareness()
        self.config = monitor.load_config()
        try:
            autostart.refresh()
        except Exception:
            pass
        self.opacity = min(1.0, max(0.2, float(self.config.get("opacity", 0.78))))

        self.root = tk.Tk()
        self.root.title("NetWatch")
        self.root.overrideredirect(True)          # 无边框、不占任务栏按钮
        self.root.attributes("-topmost", True)    # 置顶（盖在任务栏上面）
        self.root.attributes("-alpha", self.opacity)
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96)

        self.font = pick_font(self.root, 9, "bold")
        self.font_small = pick_font(self.root, 8)
        self.font_icon = pick_font(self.root, 12)

        self.collapsed = bool(self.config.get("collapsed"))
        self.snapshot = None
        self.last_ip = None
        self.change_note = ""
        self.flash_until = 0.0
        self.hover = False
        self.hidden = False
        self.popup = None
        self.ticks = 0
        self.embedded = False   # 是否已嵌入任务栏（成为任务栏的子窗口）
        self.taskbar = None
        self.quitting = False
        self.theme = self._pick_theme()
        self.bar = self._horizontal_taskbar()

        self._build()
        self._place_initial()
        self._try_embed()

        self.results = queue.Queue()
        self.checker = Checker(self.config, self.results)
        self.checker.start()
        self._poll()

    def px(self, n):
        return int(round(n * self.scale))

    # ------------------------------------------------------------ 界面
    def _build(self):
        self.body = tk.Frame(self.root)
        self.body.pack(expand=True)  # 在窗口里垂直居中

        self.toggle_btn = tk.Label(self.body, font=self.font_icon, cursor="hand2",
                                   padx=self.px(5))
        self.toggle_btn.pack(side="left")

        d = self.px(10)
        self.dot = tk.Canvas(self.body, width=d, height=d, highlightthickness=0)
        self.dot_id = self.dot.create_oval(1, 1, d - 1, d - 1, outline="")
        self.dot.pack(side="left")

        self.text = tk.Frame(self.body)
        self.place_label = tk.Label(self.text, text="检测中…", font=self.font, anchor="w",
                                    pady=0)
        self.place_label.pack(fill="x")
        self.status_label = tk.Label(self.text, font=self.font_small, anchor="w", pady=0)
        self.status_label.pack(fill="x")

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="立即刷新", command=self.refresh)
        self.menu.add_command(label="显示详情", command=self.toggle_popup)
        self.menu.add_command(label="折叠 / 展开", command=self.toggle_collapsed)
        self.menu.add_command(label="停靠到任务栏", command=self.reset_position)
        if desktop.IS_WINDOWS:
            self.embed_var = tk.BooleanVar(value=bool(self.config.get("embed_taskbar", True)))
            self.menu.add_checkbutton(label="嵌入任务栏", variable=self.embed_var,
                                      command=self.toggle_embed)
        self.autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        self.menu.add_checkbutton(label="开机自启动", variable=self.autostart_var,
                                  command=self.toggle_autostart)
        self.menu.add_separator()
        self.menu.add_command(label="修改配置…", command=self.open_config)
        self.menu.add_command(label="退出", command=self.quit)

        self._bind_mouse(self.root)
        self.root.bind("<Enter>", self._on_enter)
        self.root.bind("<Leave>", self._on_leave)

    def _bind_mouse(self, w):
        w.bind("<ButtonPress-1>", self._on_press)
        w.bind("<B1-Motion>", self._on_drag)
        w.bind("<ButtonRelease-1>", self._on_release)
        w.bind("<Button-3>", self._show_menu)
        w.bind("<Button-2>", self._show_menu)  # macOS 触控板右键
        for child in w.winfo_children():
            if not isinstance(child, (tk.Menu, tk.Toplevel)):
                self._bind_mouse(child)

    def _on_enter(self, e):
        self.hover = True
        if not self.embedded:
            self.root.attributes("-alpha", 1.0)
        self._apply_colors()

    def _on_leave(self, e):
        # 在子控件之间移动也会触发 Leave，只有真正移出窗口才恢复
        if self.root.winfo_containing(e.x_root, e.y_root) is None:
            self.hover = False
            if not self.embedded:
                self.root.attributes("-alpha", self.opacity)
            self._apply_colors()

    def _pick_theme(self):
        choice = self.config.get("theme", "auto")
        if choice not in THEMES:
            choice = "light" if desktop.is_light_theme() else "dark"
        return THEMES[choice]

    def _level(self):
        return self.snapshot.level if self.snapshot else monitor.CHECKING

    def _apply_colors(self):
        t = self.theme
        bg = t["hover"] if self.hover else t["bg"]
        for w in (self.root, self.body, self.toggle_btn, self.dot, self.text,
                  self.place_label, self.status_label):
            w.configure(bg=bg)
        if self.embedded:
            # 背景色设为透明色：平时只有文字和圆点“画”在任务栏上，悬停时才出现高亮底色
            self.root.attributes("-transparentcolor", t["bg"])
        self.toggle_btn.configure(fg=t["dim"])
        self.place_label.configure(fg=t["fg"])
        level = self._level()
        self.status_label.configure(fg=t["dim"] if level == monitor.OK else COLORS[level])

        color = COLORS[level]
        if time.time() < self.flash_until and int(time.time() * 2) % 2:
            color = bg  # IP 变化时圆点闪烁
        self.dot.itemconfigure(self.dot_id, fill=color)

    def _render(self):
        snap = self.snapshot
        if snap is None:
            self.place_label.configure(text="检测中…")
            self.status_label.configure(text="")
        else:
            self.place_label.configure(text=snap.ip.place if snap.ip else "位置未知")
            self.status_label.configure(text=snap.summary)

        # 折叠时只剩箭头和圆点；箭头指向点击后窗口变化的方向
        self.toggle_btn.configure(text="‹" if self.collapsed else "›")
        if self.collapsed:
            self.text.pack_forget()
            self.dot.pack_configure(padx=(self.px(2), self.px(10)))
        else:
            self.dot.pack_configure(padx=(self.px(2), self.px(7)))
            self.text.pack(side="left", padx=(0, self.px(10)))
        self._apply_colors()
        self._fit()
        if self.popup:
            self._fill_popup()

    # ------------------------------------------------------------ 详情弹窗
    def toggle_popup(self):
        if self.popup:
            self.popup.destroy()
            self.popup = None
            return
        self.popup = tk.Toplevel(self.root)
        self.popup.overrideredirect(True)
        self.popup.attributes("-topmost", True)
        self.popup.attributes("-alpha", max(self.opacity, 0.95))
        self.popup_body = tk.Frame(self.popup, padx=self.px(14), pady=self.px(10))
        self.popup_body.pack(fill="both", expand=True)
        self._fill_popup()

    def _fill_popup(self):
        t = self.theme
        self.popup.configure(bg=t["bg"])
        self.popup_body.configure(bg=t["bg"])
        for child in self.popup_body.winfo_children():
            child.destroy()

        def cell(text, row, col, fg=t["dim"], colspan=1, font=self.font_small, pady=0):
            tk.Label(self.popup_body, text=text, font=font, fg=fg, bg=t["bg"], anchor="w",
                     justify="left").grid(row=row, column=col, columnspan=colspan,
                                          sticky="w", padx=(0, self.px(10)), pady=pady)

        snap = self.snapshot
        level = self._level()
        row = 0
        if snap is None:
            cell("● 检测中…", row, 0, COLORS[level], 3, self.font)
        else:
            cell(f"● {snap.summary}", row, 0, COLORS[level], 3, self.font, (0, self.px(4)))
            row += 1
            ip = snap.ip
            info = [("位置", full_place(ip) if ip else "未知"), ("IP", ip.ip if ip else "—")]
            if ip and ip.isp:
                info.append(("ISP", ip.isp))
            for k, v in info:
                cell(k, row, 0)
                cell(v, row, 1, t["fg"], 2)
                row += 1

            sites = list(snap.sites) + ([snap.domestic] if snap.domestic else [])
            for i, s in enumerate(sites):
                color = COLORS[monitor.OK] if s.ok else COLORS[monitor.BAD]
                top = (self.px(6), 0) if i == 0 else 0
                cell("✓" if s.ok else "✗", row, 0, color, pady=top)
                cell(s.name + ("（国内）" if s is snap.domestic else ""), row, 1, pady=top)
                cell(f"{s.latency_ms}ms" if s.ok else s.error, row, 2,
                     t["fg"] if s.ok else color, pady=top)
                row += 1

            if self.change_note:
                cell(self.change_note, row, 0, COLORS[monitor.WARN], 3, pady=(self.px(6), 0))
                row += 1
            updated = time.strftime("%H:%M:%S", time.localtime(snap.checked_at))
            cell(f"更新于 {updated}" + (f" · {ip.source}" if ip else ""), row, 0,
                 colspan=3, pady=(self.px(6), 0))
        self._bind_popup(self.popup)
        self._place_popup()

    def _bind_popup(self, w):
        w.bind("<ButtonRelease-1>", lambda e: self.toggle_popup())
        w.bind("<Button-3>", self._show_menu)
        for child in w.winfo_children():
            self._bind_popup(child)

    def _place_popup(self):
        self.popup.update_idletasks()
        pw, ph = self.popup.winfo_reqwidth(), self.popup.winfo_reqheight()
        x, y, w, h = self.geom
        sw = self.root.winfo_screenwidth()
        px = min(max(0, x + w - pw), sw - pw)
        gap = self.px(8)
        py = y - ph - gap if y - ph - gap >= 0 else y + h + gap  # 默认弹在小部件上方
        self.popup.geometry(f"+{px}+{py}")

    # ------------------------------------------------------------ 位置
    def _horizontal_taskbar(self):
        bar = desktop.taskbar_rect()
        if bar and bar[2] - bar[0] > bar[3] - bar[1] > 0:
            return bar
        return None

    def _dock_anchor(self):
        """默认位置：任务栏托盘图标的左边；没有任务栏时放在屏幕右上角。"""
        if self.bar:
            tray = desktop.tray_rect()
            right = tray[0] - self.px(4) if tray else self.bar[2] - self.px(320)
            return right, (self.bar[1] + self.bar[3]) // 2
        return self.root.winfo_screenwidth() - self.px(12), self.px(12) + self.px(20)

    def _try_embed(self):
        if not (desktop.IS_WINDOWS and self.bar and self.config.get("embed_taskbar", True)):
            return
        self.root.attributes("-topmost", False)
        self.root.update()
        hwnd = int(self.root.wm_frame(), 16)
        bar = desktop.embed_in_taskbar(hwnd)
        if not bar:  # 嵌入失败就退回普通的置顶小窗
            self.root.attributes("-topmost", True)
            return
        self.hwnd, self.taskbar, self.embedded = hwnd, bar, True
        self.root.attributes("-alpha", 1.0)
        self._render()

    def _in_taskbar(self):
        return bool(self.bar and self.bar[1] <= self.cy <= self.bar[3])

    def _place_initial(self):
        anchor = self.config.get("anchor")
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        if isinstance(anchor, list) and len(anchor) == 2 and 0 < anchor[0] <= sw \
                and 0 <= anchor[1] < sh:
            self.right, self.cy = int(anchor[0]), int(anchor[1])
        else:
            self.right, self.cy = self._dock_anchor()
        self._render()

    def _fit(self):
        # 以右边缘为锚点：展开/折叠时向左伸缩，紧贴托盘的那一侧不动
        self.root.update_idletasks()
        w = self.body.winfo_reqwidth()
        if self._in_taskbar():
            h = max(self.px(24), self.bar[3] - self.bar[1] - self.px(8))
        else:
            h = self.px(40)
        h = max(h, self.body.winfo_reqheight())
        if self.embedded:
            # 嵌入后坐标是相对任务栏的；只能在任务栏范围内左右移动
            left, top, right, bottom = self.bar
            self.cy = (top + bottom) // 2
            x = min(max(left, self.right - w), right - w)
            y = self.cy - h // 2
            self.geom = (x, y, w, h)
            self.root.geometry(f"{w}x{h}+{x - left}+{y - top}")
            return
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = min(max(0, self.right - w), sw - w)
        y = min(max(0, self.cy - h // 2), sh - h)
        self.geom = (x, y, w, h)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def reset_position(self):
        self.bar = self._horizontal_taskbar()
        self.right, self.cy = self._dock_anchor()
        self.config["anchor"] = None
        monitor.save_config(self.config)
        self._render()

    def _on_press(self, e):
        self._press = (e.x_root, e.y_root, self.right, self.cy)
        self._moved = False

    def _on_drag(self, e):
        x0, y0, r0, c0 = self._press
        dx, dy = e.x_root - x0, e.y_root - y0
        if abs(dx) + abs(dy) > 3:
            self._moved = True
        if self._moved:
            self.right, self.cy = r0 + dx, c0 + (0 if self.embedded else dy)
            self._fit()
            if self.popup:
                self._place_popup()

    def _on_release(self, e):
        if not self._moved:
            self.toggle_collapsed() if e.widget is self.toggle_btn else self.toggle_popup()
            return
        # 拖到任务栏附近就自动垂直居中对齐
        if self.bar and self.bar[1] - self.px(20) <= self.cy <= self.bar[3] + self.px(20):
            self.cy = (self.bar[1] + self.bar[3]) // 2
        self.config["anchor"] = [self.right, self.cy]
        monitor.save_config(self.config)
        self._render()

    # ------------------------------------------------------------ 操作
    def toggle_collapsed(self):
        self.collapsed = not self.collapsed
        self.config["collapsed"] = self.collapsed
        monitor.save_config(self.config)
        self._render()

    def toggle_embed(self):
        self.config["embed_taskbar"] = self.embed_var.get()
        monitor.save_config(self.config)
        self.root.destroy()  # main() 会按新设置重新创建窗口

    def quit(self):
        self.quitting = True
        self.root.destroy()

    def refresh(self):
        self.dot.itemconfigure(self.dot_id, fill=COLORS[monitor.CHECKING])  # 灰点表示检测中
        self.checker.refresh_now()

    def toggle_autostart(self):
        want = self.autostart_var.get()
        try:
            autostart.enable() if want else autostart.disable()
        except Exception as e:
            messagebox.showerror("NetWatch", f"设置开机自启动失败：{e}")
        self.autostart_var.set(autostart.is_enabled())

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

    # ------------------------------------------------------------ 定时处理
    def _poll(self):
        try:
            while True:
                self._on_snapshot(self.results.get_nowait())
        except queue.Empty:
            pass
        if time.time() < self.flash_until + 1:
            self._apply_colors()

        self.ticks += 1
        if self.ticks % 10 == 0:  # 每 5 秒：任务栏可能移动、系统可能切换深浅色
            if self.embedded and not desktop.is_window(self.taskbar):
                self.root.destroy()  # 资源管理器重启、任务栏被重建了：交给 main() 重新嵌入
                return
            bar, theme = self._horizontal_taskbar(), self._pick_theme()
            if self.embedded and bar is None:  # 任务栏被拖到了屏幕侧边：重新创建（退回浮动模式）
                self.root.destroy()
                return
            if bar != self.bar or theme is not self.theme:
                self.bar, self.theme = bar, theme
                self._render()
            if self.embedded:
                desktop.bring_to_top(self.hwnd)

        if not self.embedded:
            # 浮动模式：全屏看视频/玩游戏时隐藏，其余时间保持在任务栏之上。
            # （嵌入模式不需要：任务栏全屏时自己会隐藏，也不会盖住我们）
            fullscreen = desktop.fullscreen_app_running()
            if fullscreen and not self.hidden:
                self.hidden = True
                if self.popup:
                    self.toggle_popup()
                self.root.withdraw()
            elif not fullscreen and self.hidden:
                self.hidden = False
                self.root.deiconify()
            if not self.hidden:
                self.root.attributes("-topmost", True)
        if self.popup:
            self.popup.attributes("-topmost", True)
        self.root.after(500, self._poll)

    def _on_snapshot(self, snap):
        ip = snap.ip
        if ip and self.last_ip and ip.ip != self.last_ip.ip:
            # IP 变了（换节点 / VPN 掉线回落到直连），圆点闪烁提醒几秒
            self.change_note = (f"IP 变化 {time.strftime('%H:%M')}: "
                                f"{self.last_ip.place} → {ip.place}")
            self.flash_until = time.time() + 6
        if ip:
            self.last_ip = ip
        self.snapshot = snap
        self._render()

    def run(self):
        try:
            self.root.mainloop()
        except tk.TclError:
            pass
        finally:
            self.checker.stop()


def main():
    # 窗口被关掉但不是用户点了“退出”（切换嵌入模式、资源管理器重启导致任务栏重建）时，
    # 等任务栏回来后重新创建窗口
    while True:
        widget = Widget()
        widget.run()
        if widget.quitting:
            break
        for _ in range(60):
            if not desktop.IS_WINDOWS or desktop.taskbar_hwnd():
                break
            time.sleep(1)
        time.sleep(1)


if __name__ == "__main__":
    main()
