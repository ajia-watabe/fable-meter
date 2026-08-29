import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "fable_plugin", os.path.join(ROOT, "plugin", "fable.10s.py"))
plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plugin)

NOW = datetime(2026, 8, 29, 21, 5, 0, tzinfo=timezone.utc)


def make_state(age_minutes=0, ok=True, fable=12, error=None):
    fetched = NOW - timedelta(minutes=age_minutes)
    return {
        "schema": 1,
        "ok": ok,
        "fetched_at": fetched.isoformat(),
        "error": error,
        "error_at": NOW.isoformat() if error else None,
        "data": {
            "fable": {"percent": fable, "resets_at": "2026-09-04T14:59:59+00:00",
                      "severity": "normal"},
            "seven_day": {"percent": 9, "resets_at": "2026-09-04T15:00:00+00:00",
                          "severity": "normal"},
            "five_hour": {"percent": 6, "resets_at": "2026-08-30T00:00:00+00:00",
                          "severity": "normal"},
            "scoped": [{"name": "Fable", "percent": fable,
                        "resets_at": "2026-09-04T14:59:59+00:00",
                        "severity": "normal"}],
            "plan": "max",
        },
    }


class FreshnessTest(unittest.TestCase):
    def test_fresh(self):
        self.assertEqual(plugin.title_line(make_state(0), NOW), "F12% W9% S6%")

    def test_stale_11_minutes(self):
        line = plugin.title_line(make_state(11), NOW)
        self.assertTrue(line.startswith("F12%? W9%? S6%?"))
        self.assertIn("color=%s" % plugin.COLOR_GRAY, line)

    def test_dead_31_minutes(self):
        line = plugin.title_line(make_state(31), NOW)
        self.assertTrue(line.startswith("F-- W-- S--"))
        self.assertIn("color=%s" % plugin.COLOR_GRAY, line)

    def test_error_while_fresh(self):
        line = plugin.title_line(make_state(3, ok=False, error="token_expired"), NOW)
        self.assertEqual(line, "F12%! W9%! S6%!")

    def test_null_data(self):
        state = make_state(0)
        state["data"] = None
        line = plugin.title_line(state, NOW)
        self.assertTrue(line.startswith("F-- W-- S--"))

    def test_missing_state_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(plugin.read_state(os.path.join(tmp, "state.json")))
        line = plugin.title_line(None, NOW)
        self.assertTrue(line.startswith("F-- W-- S--"))

    def test_broken_state_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("nope")
            self.assertIsNone(plugin.read_state(path))

    def test_state_file_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(make_state(0), fh)
            self.assertEqual(plugin.title_line(plugin.read_state(path), NOW),
                             "F12% W9% S6%")


class ColorTest(unittest.TestCase):
    def test_default_no_color(self):
        self.assertEqual(plugin.title_line(make_state(0, fable=80), NOW),
                         "F80% W9% S6%")

    def test_over_80_is_yellow(self):
        line = plugin.title_line(make_state(0, fable=81), NOW)
        self.assertIn("color=%s" % plugin.COLOR_WARN, line)

    def test_over_95_is_red(self):
        line = plugin.title_line(make_state(0, fable=96), NOW)
        self.assertIn("color=%s" % plugin.COLOR_CRIT, line)

    def test_95_exactly_is_yellow(self):
        line = plugin.title_line(make_state(0, fable=95), NOW)
        self.assertIn("color=%s" % plugin.COLOR_WARN, line)


class RenderTest(unittest.TestCase):
    def test_dropdown_contains_rows_and_actions(self):
        out = plugin.render(make_state(3), NOW, python_path="/p/python3",
                            fetch_path="/r/fetch.py")
        lines = out.split("\n")
        self.assertEqual(lines[0], "F12% W9% S6%")
        self.assertEqual(lines[1], "---")
        self.assertIn("Fable", out)
        self.assertIn("週間(全モデル)", out)
        self.assertIn("セッション(5h)", out)
        self.assertIn("プラン: max", out)
        self.assertIn("リセット", out)
        self.assertIn("今すぐ更新 | bash=/p/python3 param1=/r/fetch.py "
                      "param2=--force terminal=false refresh=true", out)
        self.assertIn("ログを開く | bash=/usr/bin/open", out)

    def test_error_row_rendered_in_red(self):
        out = plugin.render(make_state(3, ok=False, error="token_expired"), NOW)
        self.assertIn("エラー: token_expired", out)
        self.assertIn(plugin.COLOR_ERROR, out)

    def test_missing_state_renders_error_row(self):
        out = plugin.render(None, NOW)
        self.assertIn("エラー: state.json が見つからないか読めません", out)


class FormatTest(unittest.TestCase):
    def test_display_width_and_pad(self):
        self.assertEqual(plugin.display_width("Fable"), 5)
        self.assertEqual(plugin.display_width("週間(全モデル)"), 14)
        self.assertEqual(plugin.display_width("セッション(5h)"), 14)
        self.assertEqual(plugin.display_width(plugin.pad_label("Fable")), 16)
        self.assertEqual(plugin.display_width(plugin.pad_label("週間(全モデル)")), 16)

    def test_fmt_reset_japanese(self):
        self.assertIn("あと", plugin.fmt_reset("2026-09-04T23:05:00+00:00", NOW))
        self.assertTrue(
            plugin.fmt_reset("2026-08-29T20:00:00+00:00", NOW).startswith("リセット"))

    def test_fmt_duration(self):
        self.assertEqual(plugin.fmt_duration(0), "0分")
        self.assertEqual(plugin.fmt_duration(90), "1分")
        self.assertEqual(plugin.fmt_duration(3600 * 3 + 60 * 21), "3時間21分")
        self.assertEqual(plugin.fmt_duration(86400 * 5 + 3600 * 2), "5日2時間")

    def test_parse_iso_z_suffix(self):
        self.assertEqual(plugin.parse_iso("2026-08-29T16:00:00Z"),
                         datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc))
        self.assertIsNone(plugin.parse_iso(None))
        self.assertIsNone(plugin.parse_iso("nope"))

    def test_fmt_percent(self):
        self.assertEqual(plugin.fmt_percent(12), "12")
        self.assertEqual(plugin.fmt_percent(12.4), "12")
        self.assertEqual(plugin.fmt_percent(None), "--")
        self.assertEqual(plugin.fmt_percent(True), "--")


if __name__ == "__main__":
    unittest.main()
