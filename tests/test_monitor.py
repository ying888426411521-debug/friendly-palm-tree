import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import monitor  # noqa: E402
from monitor import IpInfo, SiteResult, evaluate  # noqa: E402

CONFIG = dict(monitor.DEFAULT_CONFIG)
US = IpInfo("1.2.3.4", "美国", "US", "加利福尼亚州", "洛杉矶")
CN = IpInfo("36.1.1.1", "中国", "CN", "广东", "深圳")


def ok(name, ms=200):
    return SiteResult(name, True, ms)


def fail(name):
    return SiteResult(name, False, error="超时")


class EvaluateTest(unittest.TestCase):
    def test_all_ok(self):
        snap = evaluate(US, [ok("A", 100), ok("B", 300)], ok("百度"), CONFIG)
        self.assertEqual(snap.level, monitor.OK)
        self.assertEqual(snap.summary, "正常 200ms")

    def test_some_failed(self):
        snap = evaluate(US, [ok("A"), fail("B")], ok("百度"), CONFIG)
        self.assertEqual(snap.level, monitor.WARN)

    def test_slow(self):
        snap = evaluate(US, [ok("A", 5000)], ok("百度"), CONFIG)
        self.assertEqual((snap.level, snap.summary), (monitor.WARN, "网速较慢"))

    def test_vpn_down_but_internet_up(self):
        snap = evaluate(None, [fail("A"), fail("B")], ok("百度"), CONFIG)
        self.assertEqual((snap.level, snap.summary), (monitor.BAD, "VPN 可能断开"))

    def test_vpn_fell_back_to_direct(self):
        snap = evaluate(CN, [fail("A"), fail("B")], ok("百度"), CONFIG)
        self.assertEqual((snap.level, snap.summary), (monitor.BAD, "VPN 未生效"))

    def test_direct_exit_with_partial_failure(self):
        snap = evaluate(CN, [ok("A"), fail("B")], ok("百度"), CONFIG)
        self.assertEqual(snap.level, monitor.BAD)

    def test_rule_mode_direct_ip_but_sites_ok_is_fine(self):
        snap = evaluate(CN, [ok("A"), ok("B")], ok("百度"), CONFIG)
        self.assertEqual(snap.level, monitor.OK)

    def test_offline(self):
        snap = evaluate(None, [fail("A")], fail("百度"), CONFIG)
        self.assertEqual((snap.level, snap.summary), (monitor.OFFLINE, "网络断开"))


class LookupIpTest(unittest.TestCase):
    def test_falls_back_to_next_provider(self):
        calls = []

        def fetch(url, timeout):
            calls.append(url)
            if "ip-api.com" in url:
                raise OSError("blocked")
            if "ipwho.is" in url:
                return json.dumps({"success": False}).encode()
            return json.dumps({"ip": "5.6.7.8", "country": "JP", "city": "Tokyo"}).encode()

        info = monitor.lookup_ip(1, fetch)
        self.assertEqual((info.ip, info.country_code, info.source), ("5.6.7.8", "JP", "ipinfo.io"))
        self.assertEqual(len(calls), 3)

    def test_ip_api_parsing(self):
        payload = {"status": "success", "query": "1.2.3.4", "country": "日本",
                   "countryCode": "JP", "regionName": "东京都", "city": "东京", "isp": "X"}
        info = monitor.lookup_ip(1, lambda u, t: json.dumps(payload).encode())
        self.assertEqual(info.place, "日本 · 东京")

    def test_all_fail(self):
        def fetch(url, timeout):
            raise OSError
        self.assertIsNone(monitor.lookup_ip(1, fetch))


class ConfigTest(unittest.TestCase):
    def test_creates_default_and_merges_user_values(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            self.assertEqual(monitor.load_config(path)["interval_seconds"], 30)
            self.assertTrue(os.path.exists(path))
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"interval_seconds": 10}, f)
            cfg = monitor.load_config(path)
            self.assertEqual(cfg["interval_seconds"], 10)
            self.assertEqual(cfg["opacity"], 0.78)

    def test_broken_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            with open(path, "w") as f:
                f.write("{oops")
            self.assertEqual(monitor.load_config(path)["interval_seconds"], 30)
            with open(path) as f:
                self.assertEqual(f.read(), "{oops")

    def test_moves_legacy_config_next_to_program(self):
        with tempfile.TemporaryDirectory() as d:
            legacy = os.path.join(d, "desktop", "config.json")
            new = os.path.join(d, "appdata", "NetWatch", "config.json")
            os.makedirs(os.path.dirname(legacy))
            with open(legacy, "w", encoding="utf-8") as f:
                json.dump({"interval_seconds": 12}, f)
            with mock.patch.object(monitor, "LEGACY_CONFIG_PATH", legacy), \
                    mock.patch.object(monitor, "CONFIG_PATH", new):
                self.assertEqual(monitor.load_config()["interval_seconds"], 12)
                monitor.save_config({"interval_seconds": 13})
            self.assertFalse(os.path.exists(legacy))  # 旧位置（桌面）不再有文件
            with open(new, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["interval_seconds"], 13)


if __name__ == "__main__":
    unittest.main()
