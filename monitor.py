"""网络检测逻辑（不依赖界面，可单独测试）。

- 查询当前出口 IP 及其地理位置（多个接口轮流兜底）
- 检测若干目标网站是否可达、延迟多少
- 根据结果给出整体状态：正常 / 部分异常 / VPN 可能失效 / 网络断开
"""

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, List, Optional

USER_AGENT = "Mozilla/5.0 (NetWatch widget)"

DEFAULT_CONFIG = {
    # 每隔多少秒检测一次
    "interval_seconds": 30,
    # 单个请求超时（秒）
    "timeout_seconds": 6,
    # 窗口不透明度：0.2 ~ 1.0
    "opacity": 0.78,
    # 延迟超过多少毫秒视为“慢”
    "slow_ms": 1500,
    # 出口 IP 落在这些国家/地区时报警（说明 VPN 没生效、走了直连）。不需要可设为 []
    "warn_countries": ["CN"],
    # 需要通过 VPN 访问的网站
    "targets": [
        {"name": "Google", "url": "https://www.google.com/generate_204"},
        {"name": "YouTube", "url": "https://www.youtube.com/generate_204"},
        {"name": "GitHub", "url": "https://github.com"},
        {"name": "ChatGPT", "url": "https://chatgpt.com"},
    ],
    # 国内网站，用来区分“VPN 坏了”和“整个网络断了”。不需要可设为 null
    "domestic_target": {"name": "百度", "url": "https://www.baidu.com"},
    # 窗口位置（拖动后自动保存）：[右边缘 x, 顶部 y]；null 表示屏幕右上角
    "position": None,
}


def app_dir() -> str:
    """配置文件放在程序旁边（打包成 exe 后为 exe 所在目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(app_dir(), "config.json")


def load_config(path: str = CONFIG_PATH) -> dict:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user, dict):
            config.update(user)
    except FileNotFoundError:
        save_config(config, path)
    except (OSError, ValueError):
        pass  # 配置文件写坏了就用默认值，不覆盖用户的文件
    return config


def save_config(config: dict, path: str = CONFIG_PATH) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# ---------------------------------------------------------------- 网络请求

def http_get(url: str, timeout: float) -> bytes:
    # urllib 默认会读取系统代理设置（Windows 注册表 / macOS 系统设置 / 环境变量），
    # 所以“系统代理”模式的 VPN 客户端（如 Clash）也能被正确检测。
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(256 * 1024)


@dataclass
class SiteResult:
    name: str
    ok: bool
    latency_ms: Optional[int] = None
    error: str = ""


def check_site(name: str, url: str, timeout: float) -> SiteResult:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            pass
    except urllib.error.HTTPError:
        # 收到了 HTTP 响应（哪怕是 403/404），说明网络是通的
        pass
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
        return SiteResult(name, False, error=describe_error(e))
    return SiteResult(name, True, int((time.perf_counter() - start) * 1000))


def describe_error(e: Exception) -> str:
    reason = getattr(e, "reason", e)
    text = str(reason).lower()
    if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in text:
        return "超时"
    if isinstance(reason, socket.gaierror) or "getaddrinfo" in text or "name or service" in text:
        return "DNS 失败"
    if "refused" in text:
        return "连接被拒"
    if "reset" in text:
        return "连接重置"
    if "ssl" in text or "certificate" in text:
        return "SSL 错误"
    return "无法连接"


# ---------------------------------------------------------------- IP 归属地

@dataclass
class IpInfo:
    ip: str
    country: str = ""
    country_code: str = ""
    region: str = ""
    city: str = ""
    isp: str = ""
    source: str = ""

    @property
    def place(self) -> str:
        # 窗口要小，只显示“国家 · 城市”；没有城市时退而显示省/州
        local = self.city or self.region
        parts = [self.country] + ([local] if local and local != self.country else [])
        return " · ".join(p for p in parts if p) or "未知位置"


def _parse_ip_api(d: dict) -> Optional[IpInfo]:
    if d.get("status") != "success":
        return None
    return IpInfo(d["query"], d.get("country", ""), d.get("countryCode", ""),
                  d.get("regionName", ""), d.get("city", ""), d.get("isp", ""))


def _parse_ipwhois(d: dict) -> Optional[IpInfo]:
    if not d.get("success"):
        return None
    return IpInfo(d["ip"], d.get("country", ""), d.get("country_code", ""),
                  d.get("region", ""), d.get("city", ""),
                  (d.get("connection") or {}).get("isp", ""))


def _parse_ipinfo(d: dict) -> Optional[IpInfo]:
    if not d.get("ip"):
        return None
    code = d.get("country", "")
    return IpInfo(d["ip"], code, code, d.get("region", ""), d.get("city", ""), d.get("org", ""))


def _parse_ipapi_co(d: dict) -> Optional[IpInfo]:
    if not d.get("ip") or d.get("error"):
        return None
    return IpInfo(d["ip"], d.get("country_name", ""), d.get("country_code", ""),
                  d.get("region", ""), d.get("city", ""), d.get("org", ""))


# 按顺序尝试，前面的失败了就用下一个
IP_PROVIDERS = [
    ("ip-api.com",
     "http://ip-api.com/json/?lang=zh-CN&fields=status,query,country,countryCode,regionName,city,isp",
     _parse_ip_api),
    ("ipwho.is", "https://ipwho.is/?lang=zh-CN", _parse_ipwhois),
    ("ipinfo.io", "https://ipinfo.io/json", _parse_ipinfo),
    ("ipapi.co", "https://ipapi.co/json/", _parse_ipapi_co),
]


def lookup_ip(timeout: float, fetch: Callable[[str, float], bytes] = http_get) -> Optional[IpInfo]:
    for name, url, parse in IP_PROVIDERS:
        try:
            info = parse(json.loads(fetch(url, timeout).decode("utf-8")))
        except Exception:
            continue
        if info:
            info.source = name
            return info
    return None


# ---------------------------------------------------------------- 综合判断

OK, WARN, BAD, OFFLINE, CHECKING = "ok", "warn", "bad", "offline", "checking"


@dataclass
class Snapshot:
    level: str
    summary: str
    ip: Optional[IpInfo] = None
    sites: List[SiteResult] = field(default_factory=list)
    domestic: Optional[SiteResult] = None
    checked_at: float = field(default_factory=time.time)


def evaluate(ip: Optional[IpInfo], sites: List[SiteResult],
             domestic: Optional[SiteResult], config: dict) -> Snapshot:
    slow_ms = config.get("slow_ms", 1500)
    warn_countries = {c.upper() for c in config.get("warn_countries") or []}
    ok_sites = [s for s in sites if s.ok]
    failed = [s for s in sites if not s.ok]
    slow = [s for s in ok_sites if s.latency_ms is not None and s.latency_ms > slow_ms]

    def snap(level, summary):
        return Snapshot(level, summary, ip, sites, domestic)

    # 出口 IP 在“报警国家”且有网站打不开 → 基本可以断定 VPN 没生效、走了直连。
    # （网站都能打开时不报警：VPN 的分流/规则模式下，查 IP 的请求本来就可能走直连。）
    direct_exit = bool(ip and ip.country_code.upper() in warn_countries)

    if sites and not ok_sites:
        if ip is None and (domestic is None or not domestic.ok):
            return snap(OFFLINE, "网络断开")
        return snap(BAD, "VPN 未生效" if direct_exit else "VPN 可能断开")
    if not sites and ip is None:
        return snap(OFFLINE, "网络断开")
    if failed and direct_exit:
        return snap(BAD, "VPN 未生效")
    if failed:
        return snap(WARN, f"{len(failed)} 个网站不通")
    if slow:
        return snap(WARN, "网速较慢")
    if ok_sites:
        avg = sum(s.latency_ms for s in ok_sites) // len(ok_sites)
        return snap(OK, f"正常 {avg}ms")
    return snap(OK, "正常")


def run_check(config: dict) -> Snapshot:
    timeout = float(config.get("timeout_seconds", 6))
    targets = [t for t in config.get("targets") or [] if t.get("url")]
    domestic_cfg = config.get("domestic_target")

    with ThreadPoolExecutor(max_workers=len(targets) + 2) as pool:
        ip_future = pool.submit(lookup_ip, timeout)
        site_futures = [pool.submit(check_site, t.get("name") or t["url"], t["url"], timeout)
                        for t in targets]
        domestic_future = None
        if domestic_cfg and domestic_cfg.get("url"):
            domestic_future = pool.submit(check_site, domestic_cfg.get("name", "国内"),
                                          domestic_cfg["url"], timeout)
        sites = [f.result() for f in site_futures]
        domestic = domestic_future.result() if domestic_future else None
        ip = ip_future.result()

    return evaluate(ip, sites, domestic, config)
