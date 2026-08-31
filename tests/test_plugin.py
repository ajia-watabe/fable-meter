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
        self.assertNotIn("プラン", out)
        self.assertIn("取得: ", out)
        self.assertIn("リセット", out)
        self.assertIn("リフレッシュ | bash=/p/python3 param1=/r/fetch.py "
                      "param2=--force terminal=false refresh=true "
                      "sfimage=arrow.clockwise", out)
        self.assertIn("ログを開く | bash=/usr/bin/open", out)
        self.assertIn("sfimage=doc.text", out)

    def test_info_rows_carry_no_color_param(self):
        # SwiftBar turns any row with color= into a clickable (highlightable)
        # menu item -- MenuBarItem.configureAction():
        #   if params.hasAction || params.color != nil { item.action = ... }
        # State rows must stay actionless, so colour comes from ansi=true
        # instead, which configureAction() ignores.
        out = plugin.render(make_state(3, ok=False, error="token_expired"), NOW,
                            python_path="/p/python3", fetch_path="/r/fetch.py",
                            history=[point(12, 6), point(0, 12)], dark=True)
        clickable = ("リフレッシュ", "使用量ページを開く", "ログを開く")
        info_rows = [ln for ln in out.split("\n")[2:]
                     if ln != "---" and not ln.startswith(clickable)]
        self.assertTrue(info_rows)
        for row in info_rows:
            self.assertNotIn("color=", row)
            self.assertNotIn("href=", row)
            self.assertNotIn("bash=", row)
            self.assertNotIn("refresh=true", row)
        limit_rows = [ln for ln in info_rows
                      if ln.startswith(plugin.ANSI_PRIMARY_DARK)]
        self.assertEqual(len(limit_rows), 3)
        for row in limit_rows:
            self.assertIn("ansi=true", row)
            self.assertIn("font=Menlo size=12", row)

    def test_limit_rows_are_uncoloured_in_light_appearance(self):
        # The ANSI palette has no near-black chromatic colour, and a pure grey
        # is swapped for the disabled tint by AppKit, so light mode keeps the
        # default rendering.
        out = plugin.render(make_state(3), NOW, dark=False)
        rows = [ln for ln in out.split("\n") if ln.startswith("Fable")]
        self.assertEqual(len(rows), 1)
        self.assertNotIn(plugin.ESC, rows[0])
        self.assertNotIn("ansi=true", rows[0])
        self.assertIn("font=Menlo size=12", rows[0])

    def test_appearance_comes_from_the_swiftbar_env_var(self):
        self.assertTrue(plugin.is_dark_appearance({"OS_APPEARANCE": "Dark"}))
        self.assertFalse(plugin.is_dark_appearance({"OS_APPEARANCE": "Light"}))
        self.assertFalse(plugin.is_dark_appearance({}))

    def test_no_plan_row(self):
        out = plugin.render(make_state(3), NOW)
        fetched_rows = [ln for ln in out.split("\n") if ln.startswith("取得: ")]
        self.assertEqual(len(fetched_rows), 1)
        self.assertEqual(fetched_rows[0], "取得: %s (3分前)"
                         % (NOW - timedelta(minutes=3)).astimezone().strftime(
                             "%H:%M:%S"))

    def test_no_data_row_is_plain(self):
        state = make_state(0)
        state["data"] = None
        out = plugin.render(state, NOW)
        self.assertIn("\nまだデータがありません\n", out)

    def test_error_row_is_red_via_ansi_and_stays_actionless(self):
        out = plugin.render(make_state(3, ok=False, error="token_expired"), NOW)
        row = [ln for ln in out.split("\n") if "エラー: token_expired" in ln][0]
        self.assertTrue(row.startswith(plugin.ANSI_ERROR))
        self.assertIn("ansi=true", row)
        self.assertNotIn("color=", row)
        # ANSI 31 -> NSColor.systemRed, so the ⚠️ prefix is no longer needed.
        self.assertNotIn("\u26a0", row)

    def test_missing_state_error_row_is_red(self):
        row = [ln for ln in plugin.render(None, NOW).split("\n")
               if "state.json" in ln][0]
        self.assertTrue(row.startswith(plugin.ANSI_ERROR))
        self.assertIn("ansi=true", row)

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

    def test_jitter_across_a_minute_boundary_keeps_one_window(self):
        # Real history.jsonl values: the same window is reported both just
        # before and just after 15:00, so flooring to the minute would split it.
        before = "2026-09-04T14:59:59.579336+00:00"
        after = "2026-09-04T15:00:00.051929+00:00"
        self.assertEqual(plugin.reset_key(before), plugin.reset_key(after))
        pts = [point(12, 4, resets_at=after), point(6, 8, resets_at=before),
               point(0, 12, resets_at=after)]
        self.assertEqual(plugin.window_points(pts, before), pts)
        self.assertEqual(plugin.window_points(pts, after), pts)

    def test_reset_key_of_an_unparseable_value(self):
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
        self.assertEqual(row, "予測: リセット時点 ~81%")
        self.assertNotIn("color=", row)

    def test_crossing_row_text(self):
        history = [point(10, 10), point(0, 50)]
        row = plugin.projection_row(make_state(0, fable=50), NOW, history)
        expected = (NOW + timedelta(hours=12.5)).astimezone().strftime(
            "%-m/%-d %-H時")
        self.assertEqual(row, "予測: 100%%到達 %sごろ" % expected)

    def test_collecting_row_without_any_history(self):
        self.assertEqual(plugin.projection_row(make_state(0), NOW, []),
                         "予測: データ収集中(あと約3時間)")

    def test_collecting_row_counts_down_from_the_oldest_point(self):
        # One point 70 minutes old -> 1h50m short of the 3h span -> 2h.
        row = plugin.projection_row(make_state(0), NOW,
                                    [point(70 / 60.0, 10), point(0, 12)])
        self.assertEqual(row, "予測: データ収集中(あと約2時間)")

    def test_collecting_row_never_drops_below_ten_minutes(self):
        # A single 5h-old point cannot span anything yet, but the next fetch can.
        self.assertEqual(plugin.projection_row(make_state(0), NOW,
                                               [point(5, 10)]),
                         "予測: データ収集中(あと約10分)")

    def test_collecting_row_ignores_points_of_the_previous_window(self):
        old = point(30, 90, resets_at="2026-08-28T14:59:59+00:00")
        row = plugin.projection_row(make_state(0), NOW, [old, point(1, 12)])
        self.assertEqual(row, "予測: データ収集中(あと約2時間)")

    def test_collecting_label_helper(self):
        self.assertEqual(plugin.collecting_label([], NOW), "あと約3時間")
        self.assertEqual(plugin.collecting_label([point(0, 1)], NOW), "あと約3時間")

    def test_collecting_label_uses_hours_at_or_above_90_minutes(self):
        # 2.1h left -> ceil to 3h, as before.
        self.assertEqual(plugin.collecting_label([point(0.9, 1)], NOW),
                         "あと約3時間")
        # Exactly 90 minutes is still an hours label.
        self.assertEqual(plugin.collecting_label([point(1.5, 1)], NOW),
                         "あと約2時間")

    def test_collecting_label_uses_ten_minute_steps_below_90_minutes(self):
        # 80 minutes left -> 約80分.
        self.assertEqual(plugin.collecting_label([point(100 / 60.0, 1)], NOW),
                         "あと約80分")
        # 5 minutes left -> floored at the 10-minute step.
        self.assertEqual(plugin.collecting_label([point(175 / 60.0, 1)], NOW),
                         "あと約10分")
        # Already past the span (single old point) -> still 約10分.
        self.assertEqual(plugin.collecting_label([point(9, 1)], NOW),
                         "あと約10分")
        # 61 minutes left rounds up to the next 10-minute step.
        self.assertEqual(plugin.collecting_label([point(119 / 60.0, 1)], NOW),
                         "あと約70分")

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

    def test_no_row_once_the_reset_has_passed(self):
        state = make_state(0)
        state["data"]["fable"]["resets_at"] = (
            NOW - timedelta(hours=1)).isoformat()
        self.assertIsNone(plugin.projection_row(state, NOW, self.history()))

    def test_render_places_the_row_after_the_three_limit_rows(self):
        out = plugin.render(make_state(0), NOW, python_path="/p/python3",
                            fetch_path="/r/fetch.py", history=self.history())
        lines = out.split("\n")
        self.assertTrue(lines[4].startswith("セッション(5h)"))
        self.assertTrue(lines[5].startswith("予測: "))
        self.assertEqual(lines[6], "---")

    def test_render_shows_the_collecting_row_without_history(self):
        out = plugin.render(make_state(0), NOW, python_path="/p/python3",
                            fetch_path="/r/fetch.py", history=[])
        self.assertIn("\n予測: データ収集中(あと約3時間)\n", out)

    def test_render_omits_the_row_for_a_stale_state(self):
        out = plugin.render(make_state(11), NOW, python_path="/p/python3",
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


# ---------------------------------------------------------------- 適応予測

def aligned(dt):
    """dt をローカルの時刻境界に丸める(バケットはローカル時刻なのでテストも合わせる)。"""
    local = dt.astimezone()
    return local.replace(minute=0, second=0, microsecond=0)


def at(when, percent, resets_at=RESETS_AT):
    return {"t": when, "fable": float(percent), "fable_resets_at": resets_at}


def flat_curve():
    return [1.0 / 24] * 24


def half_day_curve(start_hour):
    """start_hour から 12 時間だけ活動するカーブ(残り 12 時間は share 0)。"""
    shares = [0.0] * 24
    for i in range(12):
        shares[(start_hour + i) % 24] = 1.0 / 12
    return shares


class WeightedSlopeTest(unittest.TestCase):
    def test_recent_pace_dominates_on_an_accelerating_series(self):
        # 24h で 20% だが、後半 6h だけで 10% 伸びている。
        points = [point(24, 0), point(18, 1), point(12, 2),
                  point(6, 10), point(0, 20)]
        endpoint = 20.0 / 24
        slope = plugin.pace_slope(points)
        self.assertGreater(slope, endpoint)
        self.assertLess(slope, 2.0)

    def test_late_usage_beats_early_usage_with_the_same_endpoints(self):
        # 同じ 24h・同じ 0% -> 20% でも、消費が直近に寄っている方が速いと見なす。
        early = [point(24, 0), point(18, 18), point(12, 19),
                 point(6, 19), point(0, 20)]
        late = [point(24, 0), point(18, 1), point(12, 1),
                point(6, 2), point(0, 20)]
        self.assertGreater(plugin.pace_slope(late), plugin.pace_slope(early))
        # 端点だけを見る従来の傾きは両者を区別できない。
        self.assertAlmostEqual((20.0 - 0.0) / 24, 20.0 / 24, places=9)

    def test_two_points_reproduce_the_endpoint_slope(self):
        # 2 点なら重み付き最小二乗も 2 点をちょうど通る = 従来と同じ傾き。
        self.assertAlmostEqual(plugin.pace_slope([point(12, 6), point(0, 12)]),
                               0.5, places=9)

    def test_slope_is_floored_at_zero(self):
        self.assertEqual(plugin.pace_slope([point(12, 40), point(0, 30)]), 0.0)

    def test_equal_timestamps_do_not_crash(self):
        self.assertEqual(plugin.weighted_slope([1.0, 1.0], [2.0, 3.0],
                                               [0.0, 0.0]), 0.0)
        self.assertEqual(plugin.pace_slope([point(0, 5), point(0, 9)]), 0.0)

    def test_half_life_shortens_the_memory(self):
        points = [point(24, 0), point(18, 1), point(12, 2),
                  point(6, 10), point(0, 20)]
        xs = [(points[-1]["t"] - p["t"]).total_seconds() / -3600.0
              for p in points]
        ys = [p["fable"] for p in points]
        ages = [(points[-1]["t"] - p["t"]).total_seconds() / 3600.0
                for p in points]
        short = plugin.weighted_slope(xs, ys, ages, half_life=3.0)
        long_ = plugin.weighted_slope(xs, ys, ages, half_life=48.0)
        self.assertGreater(short, long_)
        # 重みが 1 点に潰れても例外にはせず 0 を返す。
        self.assertEqual(plugin.weighted_slope(xs, ys, ages, half_life=1e-9),
                         0.0)


class ActivityCurveTest(unittest.TestCase):
    def setUp(self):
        self.base = aligned(NOW) - timedelta(days=2)
        self.hour = self.base.hour

    def two_day_history(self, extra=()):
        # 90 分の消費(30 分 + 60 分)を時刻境界をまたいで記録する。
        entries = [at(self.base + timedelta(minutes=30), 0),
                   at(self.base + timedelta(hours=2), 3)]
        entries.extend(extra)
        # 24 時間以上の観測にするための末尾(消費なし)。
        entries.append(at(self.base + timedelta(hours=30), 3))
        return entries

    def test_delta_is_split_across_hour_boundaries_and_normalized(self):
        shares = plugin.activity_curve(self.two_day_history())
        self.assertIsNotNone(shares)
        self.assertAlmostEqual(sum(shares), 1.0, places=9)
        self.assertAlmostEqual(shares[self.hour], 1.0 / 3, places=9)
        self.assertAlmostEqual(shares[(self.hour + 1) % 24], 2.0 / 3, places=9)
        others = [s for h, s in enumerate(shares)
                  if h not in (self.hour, (self.hour + 1) % 24)]
        self.assertEqual(set(others), {0.0})

    def test_short_history_has_no_curve(self):
        entries = [at(self.base, 0), at(self.base + timedelta(hours=23), 5)]
        self.assertIsNone(plugin.activity_curve(entries))

    def test_flat_history_has_no_curve(self):
        entries = [at(self.base, 5), at(self.base + timedelta(hours=30), 5)]
        self.assertIsNone(plugin.activity_curve(entries))

    def test_pairs_that_cross_a_window_are_ignored(self):
        other = "2026-08-28T15:00:00+00:00"
        entries = [at(self.base, 90, resets_at=other),
                   at(self.base + timedelta(hours=1), 2),
                   at(self.base + timedelta(hours=30), 2)]
        # 窓またぎの差分(-88)も % 低下も拾わないので、消費は 0 = カーブ無し。
        self.assertIsNone(plugin.activity_curve(entries))

    def test_second_day_adds_to_the_same_buckets(self):
        extra = [at(self.base + timedelta(hours=24, minutes=30), 3),
                 at(self.base + timedelta(hours=25), 6)]
        shares = plugin.activity_curve(self.two_day_history(extra=extra))
        # 1日目: h に 1、h+1 に 2。2日目: h に 3。合計 6 → h=4/6, h+1=2/6。
        self.assertAlmostEqual(shares[self.hour], 4.0 / 6, places=9)
        self.assertAlmostEqual(shares[(self.hour + 1) % 24], 2.0 / 6, places=9)

    def test_too_few_entries(self):
        self.assertIsNone(plugin.activity_curve([]))
        self.assertIsNone(plugin.activity_curve([at(self.base, 1)]))


class EffectiveHoursTest(unittest.TestCase):
    def setUp(self):
        self.start = aligned(NOW)

    def test_no_curve_is_wallclock(self):
        self.assertAlmostEqual(
            plugin.effective_hours(None, self.start,
                                   self.start + timedelta(hours=7.5)),
            7.5, places=9)

    def test_flat_curve_equals_wallclock(self):
        self.assertAlmostEqual(
            plugin.effective_hours(flat_curve(), self.start,
                                   self.start + timedelta(hours=7.5)),
            7.5, places=9)

    def test_active_hours_stretch_and_dead_hours_vanish(self):
        curve = half_day_curve(self.start.hour)
        # 活動 12 時間に集中 = 活動中は 2 倍速。
        self.assertAlmostEqual(
            plugin.effective_hours(curve, self.start,
                                   self.start + timedelta(hours=4)),
            8.0, places=9)
        night = self.start + timedelta(hours=12)
        self.assertAlmostEqual(
            plugin.effective_hours(curve, night, night + timedelta(hours=4)),
            0.0, places=9)

    def test_a_full_day_is_always_24_effective_hours(self):
        curve = half_day_curve((self.start.hour + 3) % 24)
        self.assertAlmostEqual(
            plugin.effective_hours(curve, self.start,
                                   self.start + timedelta(hours=24)),
            24.0, places=6)

    def test_empty_or_reversed_span(self):
        self.assertEqual(plugin.effective_hours(flat_curve(), self.start,
                                                self.start), 0.0)
        self.assertEqual(plugin.effective_hours(flat_curve(), self.start,
                                                self.start - timedelta(hours=1)),
                         0.0)


class CurveProjectionTest(unittest.TestCase):
    def setUp(self):
        self.now = aligned(NOW)
        # 直近 6 時間と、その後 6 時間だけが活動時間。
        self.curve = half_day_curve((self.now.hour - 6) % 24)
        self.points = [at(self.now - timedelta(hours=6), 20),
                       at(self.now, 50)]

    def test_curve_shrinks_a_night_heavy_remainder(self):
        resets_at = self.now + timedelta(hours=8)
        # A のみ: 5%/h * 8h = +40 -> 90%
        kind, plain = plugin.project(self.points, resets_at, self.now)
        self.assertEqual((kind, round(plain)), ("reset", 90))
        # A+B: 実効傾き 2.5%/実効h、残り実効 12h -> +30 -> 80%
        kind, curved = plugin.project(self.points, resets_at, self.now,
                                      shares=self.curve)
        self.assertEqual((kind, round(curved)), ("reset", 80))
        self.assertLess(curved, plain)

    def test_a_flat_curve_matches_the_wallclock_projection(self):
        resets_at = self.now + timedelta(hours=8)
        _, plain = plugin.project(self.points, resets_at, self.now)
        _, curved = plugin.project(self.points, resets_at, self.now,
                                   shares=flat_curve())
        self.assertAlmostEqual(plain, curved, places=6)

    def test_dead_remainder_projects_no_growth(self):
        night = self.now + timedelta(hours=6)
        points = [at(night - timedelta(hours=6), 20), at(night, 50)]
        # 6h 先から 12h は完全な非活動時間: 増えないのが正しい。
        kind, value = plugin.project(points, night + timedelta(hours=10),
                                     night, shares=self.curve)
        self.assertEqual((kind, value), ("reset", 50.0))

    def test_crossing_walks_forward_through_the_curve(self):
        # 実効 2.5%/h、残り 50% -> 実効 20h。活動 6h(=12) + 死んだ 12h(=0)
        # + 活動 4h(=8) で到達 = 22 時間後。
        kind, when = plugin.project(self.points, self.now + timedelta(hours=48),
                                    self.now, shares=self.curve)
        self.assertEqual(kind, "cross")
        self.assertEqual(when, self.now + timedelta(hours=22))

    def test_crossing_without_a_curve_is_linear(self):
        kind, when = plugin.project(self.points, self.now + timedelta(hours=48),
                                    self.now)
        self.assertEqual(kind, "cross")
        self.assertEqual(when, self.now + timedelta(hours=10))

    def test_unreachable_crossing_falls_back_to_the_value(self):
        # 到達までに歩ける時間の上限を超えるケースでは値だけを返す。
        curve = [0.0] * 24
        curve[(self.now.hour + 1) % 24] = 1.0
        points = [at(self.now - timedelta(hours=6), 49),
                  at(self.now, 50)]
        result = plugin.project(points, self.now + timedelta(hours=8),
                                self.now, shares=curve)
        self.assertEqual(result[0], "reset")

    def test_advance_effective_returns_none_when_never_reached(self):
        self.assertIsNone(plugin.advance_effective([0.0] * 24, self.now, 1.0))
        self.assertEqual(plugin.advance_effective(None, self.now, 0.0), self.now)


class ProjectionGateTest(unittest.TestCase):
    def setUp(self):
        self.now = aligned(NOW) + timedelta(minutes=5)
        self.resets = "2026-09-04T14:59:59+00:00"

    def state(self, fable):
        state = make_state(0, fable=fable)
        state["fetched_at"] = self.now.isoformat()
        return state

    def history(self, hours):
        start = self.now - timedelta(hours=hours)
        return [at(start + timedelta(hours=h), h * 0.1, resets_at=self.resets)
                for h in range(int(hours) + 1)]

    def test_short_history_falls_back_to_component_a(self):
        history = self.history(6)
        self.assertIsNone(plugin.activity_curve(history))
        row = plugin.projection_row(self.state(6), self.now, history)
        kind, value = plugin.project(history, plugin.parse_iso(self.resets),
                                     self.now)
        self.assertEqual(kind, "reset")
        self.assertEqual(row, "予測: リセット時点 ~%d%%" % round(value))

    def test_long_history_activates_the_curve(self):
        history = self.history(30)
        self.assertIsNotNone(plugin.activity_curve(history))
        row = plugin.projection_row(self.state(30), self.now, history)
        self.assertTrue(row.startswith("予測: "))
        self.assertNotIn("収集中", row)

    def test_two_hours_of_history_is_still_collecting(self):
        history = self.history(2)
        row = plugin.projection_row(self.state(2), self.now, history)
        self.assertTrue(row.startswith("予測: データ収集中("))


if __name__ == "__main__":
    unittest.main()
