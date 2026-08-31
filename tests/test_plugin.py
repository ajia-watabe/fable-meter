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
        self.assertIn("リフレッシュ | bash=/p/python3 param1=/r/fetch.py "
                      "param2=--force terminal=false refresh=true "
                      "sfimage=arrow.clockwise", out)
        self.assertIn("ログを開く | bash=/usr/bin/open", out)
        self.assertIn("sfimage=doc.text", out)

    def test_info_rows_have_explicit_colors(self):
        out = plugin.render(make_state(3), NOW, python_path="/p/python3",
                            fetch_path="/r/fetch.py")
        limit_rows = [ln for ln in out.split("\n")
                      if ln.startswith(("Fable", "週間(全モデル)", "セッション(5h)"))]
        self.assertEqual(len(limit_rows), 3)
        for row in limit_rows:
            self.assertIn("font=Menlo size=12", row)
            self.assertIn("color=%s" % plugin.COLOR_INFO, row)
        plan_row = [ln for ln in out.split("\n") if ln.startswith("プラン: ")]
        self.assertEqual(len(plan_row), 1)
        self.assertIn("color=%s" % plugin.COLOR_SECONDARY, plan_row[0])

    def test_dual_mode_color_format(self):
        self.assertEqual(plugin.COLOR_INFO, "#1d1d1f,#e8e8ed")
        self.assertEqual(plugin.COLOR_SECONDARY, "#6e6e73,#98989d")

    def test_no_data_row_uses_secondary_color(self):
        state = make_state(0)
        state["data"] = None
        out = plugin.render(state, NOW)
        self.assertIn("まだデータがありません | color=%s" % plugin.COLOR_SECONDARY,
                      out)

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


RESETS_AT = "2026-09-04T14:59:59+00:00"
RESETS_DT = datetime(2026, 9, 4, 14, 59, 59, tzinfo=timezone.utc)


def point(hours_ago, percent, resets_at=RESETS_AT):
    return {"t": NOW - timedelta(hours=hours_ago), "fable": float(percent),
            "fable_resets_at": resets_at}


class ProjectionMathTest(unittest.TestCase):
    def test_normal_linear_projection(self):
        # 6% over 12h -> 0.5%/h; 137.0h remain to the reset -> ~12 + 68.5
        points = [point(12, 6), point(0, 12)]
        kind, value = plugin.project(points, RESETS_DT, NOW)
        self.assertEqual(kind, "reset")
        self.assertEqual(int(round(value)), 81)

    def test_flat_usage_projects_the_current_value(self):
        kind, value = plugin.project([point(24, 30), point(0, 30)],
                                     RESETS_DT, NOW)
        self.assertEqual((kind, int(round(value))), ("reset", 30))

    def test_projection_never_falls_below_the_latest_value(self):
        # A decreasing slope inside one window would project downwards; clamp.
        kind, value = plugin.project([point(24, 40), point(0, 30)],
                                     RESETS_DT, NOW)
        self.assertEqual((kind, int(round(value))), ("reset", 30))

    def test_already_past_100_reports_a_value_clamped_at_200(self):
        # No future crossing to report once the latest point is already >= 100.
        kind, value = plugin.project([point(4, 150), point(0, 199)],
                                     NOW + timedelta(hours=1), NOW)
        self.assertEqual(kind, "reset")
        self.assertEqual(value, plugin.PROJECTION_MAX_PERCENT)

    def test_insufficient_points(self):
        self.assertIsNone(plugin.project([], RESETS_DT, NOW))
        self.assertIsNone(plugin.project([point(0, 12)], RESETS_DT, NOW))

    def test_span_shorter_than_three_hours(self):
        self.assertIsNone(plugin.project([point(2.9, 5), point(0, 9)],
                                         RESETS_DT, NOW))
        self.assertIsNotNone(plugin.project([point(3.0, 5), point(0, 9)],
                                            RESETS_DT, NOW))

    def test_no_projection_after_the_reset_has_passed(self):
        self.assertIsNone(plugin.project([point(12, 6), point(0, 12)],
                                         NOW - timedelta(hours=1), NOW))

    def test_projected_over_100_returns_crossing_time(self):
        # 40% over 10h -> 4%/h; from 50% it takes 12.5h to reach 100%.
        points = [point(10, 10), point(0, 50)]
        kind, value = plugin.project(points, RESETS_DT, NOW)
        self.assertEqual(kind, "cross")
        self.assertEqual(value, NOW + timedelta(hours=12.5))


class WindowPointsTest(unittest.TestCase):
    def test_all_points_of_one_window_are_kept(self):
        pts = [point(12, 4), point(6, 8), point(0, 12)]
        self.assertEqual(plugin.window_points(pts, RESETS_AT), pts)

    def test_sub_second_jitter_in_resets_at_is_ignored(self):
        # The API returns a slightly different fractional second every fetch.
        pts = [point(12, 4, resets_at="2026-09-04T14:59:59.051929+00:00"),
               point(6, 8, resets_at="2026-09-04T14:59:59.487294+00:00"),
               point(0, 12, resets_at="2026-09-04T14:59:59.127522+00:00")]
        self.assertEqual(plugin.window_points(pts, RESETS_AT), pts)
        self.assertEqual(plugin.reset_key("2026-09-04T14:59:59.9+00:00"),
                         plugin.reset_key(RESETS_AT))
        self.assertIsNone(plugin.reset_key(None))
        self.assertIsNone(plugin.reset_key("nope"))

    def test_points_from_the_previous_window_are_discarded(self):
        old = point(30, 90, resets_at="2026-08-28T14:59:59+00:00")
        pts = [old, point(12, 4), point(0, 12)]
        self.assertEqual(plugin.window_points(pts, RESETS_AT), pts[1:])

    def test_percent_drop_ends_the_window_even_without_resets_at(self):
        pts = [point(30, 90, resets_at=None), point(12, 4, resets_at=None),
               point(0, 12, resets_at=None)]
        self.assertEqual(plugin.window_points(pts, RESETS_AT), pts[1:])

    def test_projection_across_a_reset_is_refused_for_lack_of_span(self):
        # Only one point survives the reset boundary -> no projection.
        pts = [point(30, 90), point(0, 12)]
        kept = plugin.window_points(pts, RESETS_AT)
        self.assertEqual(len(kept), 1)
        self.assertIsNone(plugin.project(kept, RESETS_DT, NOW))


class ProjectionRowTest(unittest.TestCase):
    def history(self):
        return [point(12, 6), point(0, 12)]

    def test_row_rendered_for_fresh_ok_state(self):
        row = plugin.projection_row(make_state(0), NOW, self.history())
        self.assertTrue(row.startswith("予測: リセット時点 ~81%"))
        self.assertIn("color=%s" % plugin.COLOR_SECONDARY, row)

    def test_crossing_row_text(self):
        history = [point(10, 10), point(0, 50)]
        row = plugin.projection_row(make_state(0, fable=50), NOW, history)
        expected = (NOW + timedelta(hours=12.5)).astimezone().strftime(
            "%-m/%-d %-H時")
        self.assertEqual(row.split(" | ")[0], "予測: 100%%到達 %sごろ" % expected)

    def test_no_row_without_enough_history(self):
        self.assertIsNone(plugin.projection_row(make_state(0), NOW, []))
        self.assertIsNone(plugin.projection_row(make_state(0), NOW,
                                                [point(0, 12)]))

    def test_no_row_when_stale(self):
        self.assertIsNone(plugin.projection_row(make_state(11), NOW,
                                                self.history()))
        self.assertIsNone(plugin.projection_row(make_state(31), NOW,
                                                self.history()))

    def test_no_row_when_last_fetch_failed(self):
        state = make_state(1, ok=False, error="network_error")
        self.assertIsNone(plugin.projection_row(state, NOW, self.history()))

    def test_no_row_without_state_or_data(self):
        self.assertIsNone(plugin.projection_row(None, NOW, self.history()))
        state = make_state(0)
        state["data"] = None
        self.assertIsNone(plugin.projection_row(state, NOW, self.history()))

    def test_no_row_without_resets_at(self):
        state = make_state(0)
        state["data"]["fable"]["resets_at"] = None
        self.assertIsNone(plugin.projection_row(state, NOW, self.history()))

    def test_render_places_the_row_after_the_three_limit_rows(self):
        out = plugin.render(make_state(0), NOW, python_path="/p/python3",
                            fetch_path="/r/fetch.py", history=self.history())
        lines = out.split("\n")
        self.assertTrue(lines[4].startswith("セッション(5h)"))
        self.assertTrue(lines[5].startswith("予測: "))
        self.assertEqual(lines[6], "---")

    def test_render_omits_the_row_without_history(self):
        out = plugin.render(make_state(0), NOW, python_path="/p/python3",
                            fetch_path="/r/fetch.py", history=[])
        self.assertNotIn("予測:", out)


class HistoryReadTest(unittest.TestCase):
    def test_reads_sorts_and_skips_bad_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "history.jsonl")
            rows = [
                json.dumps({"t": (NOW - timedelta(hours=1)).isoformat(),
                            "fable": 11, "fable_resets_at": RESETS_AT}),
                json.dumps({"t": (NOW - timedelta(hours=5)).isoformat(),
                            "fable": 6, "fable_resets_at": RESETS_AT}),
                "{broken", "", "[1]",
                json.dumps({"t": "nope", "fable": 1}),
                json.dumps({"t": NOW.isoformat(), "fable": None}),
            ]
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(rows) + "\n")
            entries = plugin.read_history(path)
            self.assertEqual([e["fable"] for e in entries], [6.0, 11.0])

    def test_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(plugin.read_history(os.path.join(tmp, "no.jsonl")),
                             [])


class UsagePageActionTest(unittest.TestCase):
    def test_row_follows_refresh(self):
        out = plugin.render(make_state(3), NOW, python_path="/p/python3",
                            fetch_path="/r/fetch.py")
        lines = out.split("\n")
        idx = [i for i, ln in enumerate(lines) if ln.startswith("リフレッシュ")][0]
        self.assertEqual(
            lines[idx + 1],
            "使用量ページを開く | href=https://claude.ai/settings/usage "
            "sfimage=safari")
        self.assertTrue(lines[idx + 2].startswith("ログを開く"))


if __name__ == "__main__":
    unittest.main()
