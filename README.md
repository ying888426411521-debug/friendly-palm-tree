# NetWatch · 网络/VPN 状态小窗

一个常驻屏幕右上角的**半透明小窗口**，实时显示：

- 当前出口 IP 的**地点**（国家 · 城市）
- 网络/VPN 是否正常：常用网站能不能打开、延迟多少
- VPN 掉线、走了直连、换了节点时**变色 + 闪烁提醒**

![预览](docs/preview.png)

## 状态说明

| 圆点 | 文字 | 含义 |
|---|---|---|
| 🟢 绿 | `正常 234ms` | 所有网站都能打开，显示平均延迟 |
| 🟡 黄 | `1 个网站不通` / `网速较慢` | 部分网站打不开，或延迟超过 1.5 秒 |
| 🔴 红 | `VPN 可能断开` | 外网全都打不开，但国内网络正常 |
| 🔴 红 | `VPN 未生效` | 出口 IP 在国内（默认 CN）且外网打不开 —— 多半是 VPN 掉了走了直连 |
| ⚪ 灰 | `网络断开` | 连国内网站都打不开 |

出口 IP 一旦变化（换节点、VPN 掉线），窗口会闪烁几秒，展开详情里能看到「IP 变化：美国 · 洛杉矶 → 日本 · 东京」。

## 下载（Windows，免安装）

到 GitHub 仓库的 **Releases → latest** 下载 `NetWatch.exe`，双击运行即可，不需要安装 Python。
每次推送代码都会由 GitHub Actions 在 Windows 上自动重新打包（见 `.github/workflows/build-exe.yml`）。

> 首次运行时 Windows 可能弹出「Windows 已保护你的电脑」（exe 没有数字签名），点「更多信息 → 仍要运行」即可。

## 从源码运行

需要 **Python 3.8+**（Windows / macOS 官方安装包自带 tkinter，无需安装其它依赖）。

- **Windows**：双击 `run_windows.bat`（不会出现黑色命令行窗口）
- **macOS / Linux**：`./run_mac_linux.sh`，或 `python3 netwatch.py`

> Linux 如果提示缺少 tkinter：`sudo apt install python3-tk`

### 操作

| 操作 | 作用 |
|---|---|
| 左键单击 | 展开 / 收起详情（每个网站的延迟、ISP、更新时间） |
| 左键拖动 | 移动窗口，位置自动记住 |
| 鼠标悬停 | 暂时变为不透明，方便看清 |
| 右键 | 菜单：立即刷新 / 回到右上角 / 开机自启动 / 修改配置 / 退出 |

### 开机自启动

右键 → 勾选「**开机自启动**」即可，再点一次取消。只对当前用户生效，不需要管理员权限：

- Windows：写入注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`（也可以在「任务管理器 → 启动应用」里看到/关闭）
- macOS：`~/Library/LaunchAgents/com.netwatch.plist`
- Linux：`~/.config/autostart/netwatch.desktop`

开启后如果把 exe 挪到了别的文件夹，下次手动打开一次，启动项会自动更新为新位置。

### 打包成 exe（可选）

```bat
pip install pyinstaller
build_exe.bat
```

生成的 `dist\NetWatch.exe` 可以单独拷贝使用，配置文件会放在 exe 旁边。

## 配置

第一次运行会在程序旁边生成 `config.json`（右键 → 修改配置 可直接打开），改完后重启小窗生效：

```jsonc
{
  "interval_seconds": 30,        // 检测间隔（秒）
  "timeout_seconds": 6,          // 单次请求超时（秒）
  "opacity": 0.78,               // 不透明度 0.2 ~ 1.0
  "slow_ms": 1500,               // 超过这个延迟算“慢”
  "warn_countries": ["CN"],      // 出口 IP 在这些国家且外网不通时，判定为“VPN 未生效”
  "targets": [                   // 需要通过 VPN 访问的网站，可自行增删
    {"name": "Google",  "url": "https://www.google.com/generate_204"},
    {"name": "YouTube", "url": "https://www.youtube.com/generate_204"},
    {"name": "GitHub",  "url": "https://github.com"},
    {"name": "ChatGPT", "url": "https://chatgpt.com"}
  ],
  "domestic_target": {"name": "百度", "url": "https://www.baidu.com"},  // 用于判断是不是整个网络断了
  "position": null               // 窗口位置，拖动后自动保存
}
```

## 原理与注意事项

- **IP 归属地**依次尝试 ip-api.com、ipwho.is、ipinfo.io、ipapi.co，一个失败自动换下一个。
- **网站检测**：只要收到 HTTP 响应（哪怕是 403）就算“通”，超时 / DNS 失败 / 连接被重置才算“不通”。
- 程序会自动使用系统代理设置，所以无论 VPN 是 **TUN/全局模式** 还是 **系统代理模式**（Clash、v2rayN 等）都能检测。
- 如果 VPN 用的是**规则/分流模式**，查询 IP 的请求可能走直连，此时显示的会是你本地的 IP 和地点；只要外网网站都能打开，圆点仍然是绿色，不会误报。

## 开发

```bash
python3 -m unittest discover -s tests   # 运行测试（检测逻辑，不需要图形界面）
```

- `monitor.py`：检测逻辑（IP 查询、网站检测、状态判断），不依赖界面
- `netwatch.py`：tkinter 小窗界面
- `autostart.py`：开机自启动（注册表 / LaunchAgent / autostart）
