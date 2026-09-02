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


def point(hours_ago, percent, resets_at=RESETS_AT, seven_day=None):
    return {"t": NOW - timedelta(hours=hours_ago), "fable": float(percent),
            "seven_day": None if seven_day is None else float(seven_day),
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
        return [point(12, 6, seven_day=4), point(0, 12, seven_day=8)]

    def test_one_row_per_metric_for_a_fresh_ok_state(self):
        rows = plugin.projection_rows(make_state(0), NOW, self.history())
        # Fable: 0.5%/h * 137.9h -> 81。週間: 0.333%/h * 137.9h -> 54。
        self.assertEqual(rows, ["予測 Fable      リセット時点 ~81%",
                                "予測 週間       リセット時点 ~54%"])
        for row in rows:
            self.assertNotIn("color=", row)

    def test_rows_line_up_with_the_limit_rows(self):
        rows = plugin.projection_rows(make_state(0), NOW, self.history())
        widths = {plugin.display_width(r.split("リセット時点")[0]) for r in rows}
        self.assertEqual(widths, {plugin.LABEL_WIDTH})

    def test_crossing_row_text(self):
        history = [point(10, 10, seven_day=10), point(0, 50, seven_day=50)]
        rows = plugin.projection_rows(make_state(0, fable=50), NOW, history)
        expected = (NOW + timedelta(hours=12.5)).astimezone().strftime(
            "%-m/%-d %-H時")
        self.assertEqual(rows[0],
                         "予測 Fable      100%%到達 %sごろ" % expected)
        self.assertTrue(rows[1].startswith("予測 週間       100%到達 "))

    def test_seven_day_projects_from_its_own_values(self):
        # 同じ点でも Fable と週間で伸び方が違えば別々の予測になる。
        history = [point(12, 6, seven_day=1), point(0, 12, seven_day=2)]
        rows = plugin.projection_rows(make_state(0), NOW, history)
        self.assertEqual(rows[0], "予測 Fable      リセット時点 ~81%")
        # 1%/12h = 0.0833%/h * 137.9h -> 2 + 11.5 = ~13%
        self.assertEqual(rows[1], "予測 週間       リセット時点 ~13%")

    def test_a_metric_without_history_values_shows_its_own_collecting_row(self):
        # 片方だけ予測できるとき(履歴に seven_day が無い等)は混ぜて出す。
        rows = plugin.projection_rows(make_state(0), NOW,
                                      [point(12, 6), point(0, 12)])
        self.assertEqual(rows, ["予測 Fable      リセット時点 ~81%",
                                "予測 週間       データ収集中(あと約3時間)"])

    def test_collecting_row_without_any_history(self):
        self.assertEqual(plugin.projection_rows(make_state(0), NOW, []),
                         ["予測: データ収集中(あと約3時間)"])

    def test_collecting_row_counts_down_from_the_oldest_point(self):
        # One point 70 minutes old -> 1h50m short of the 3h span -> 2h.
        rows = plugin.projection_rows(
            make_state(0), NOW,
            [point(70 / 60.0, 10, seven_day=8), point(0, 12, seven_day=9)])
        self.assertEqual(rows, ["予測: データ収集中(あと約2時間)"])

    def test_collecting_row_never_drops_below_ten_minutes(self):
        # A single 5h-old point cannot span anything yet, but the next fetch can.
        self.assertEqual(plugin.projection_rows(make_state(0), NOW,
                                                [point(5, 10, seven_day=8)]),
                         ["予測: データ収集中(あと約10分)"])

    def test_collecting_row_ignores_points_of_the_previous_window(self):
        old = point(30, 90, resets_at="2026-08-28T14:59:59+00:00", seven_day=80)
        rows = plugin.projection_rows(make_state(0), NOW,
                                      [old, point(1, 12, seven_day=9)])
        self.assertEqual(rows, ["予測: データ収集中(あと約2時間)"])

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

    def test_no_rows_when_stale(self):
        self.assertEqual(plugin.projection_rows(make_state(11), NOW,
                                                self.history()), [])
        self.assertEqual(plugin.projection_rows(make_state(31), NOW,
                                                self.history()), [])

    def test_no_rows_when_last_fetch_failed(self):
        state = make_state(1, ok=False, error="network_error")
        self.assertEqual(plugin.projection_rows(state, NOW, self.history()), [])

    def test_no_rows_without_state_or_data(self):
        self.assertEqual(plugin.projection_rows(None, NOW, self.history()), [])
        state = make_state(0)
        state["data"] = None
        self.assertEqual(plugin.projection_rows(state, NOW, self.history()), [])

    def test_metric_without_resets_at_is_dropped(self):
        state = make_state(0)
        state["data"]["fable"]["resets_at"] = None
        rows = plugin.projection_rows(state, NOW, self.history())
        self.assertEqual(rows, ["予測 週間       リセット時点 ~54%"])
        state["data"]["seven_day"]["resets_at"] = None
        self.assertEqual(plugin.projection_rows(state, NOW, self.history()), [])

    def test_no_rows_once_the_resets_have_passed(self):
        state = make_state(0)
        past = (NOW - timedelta(hours=1)).isoformat()
        state["data"]["fable"]["resets_at"] = past
        state["data"]["seven_day"]["resets_at"] = past
        self.assertEqual(plugin.projection_rows(state, NOW, self.history()), [])

    def test_render_places_the_rows_after_the_three_limit_rows(self):
        out = plugin.render(make_state(0), NOW, python_path="/p/python3",
                            fetch_path="/r/fetch.py", history=self.history())
        lines = out.split("\n")
        self.assertTrue(lines[4].startswith("セッション(5h)"))
        self.assertTrue(lines[5].startswith("予測 Fable"))
        self.assertTrue(lines[6].startswith("予測 週間"))
        # 3 枠と桁を揃えるため予測行も等幅にする。
        self.assertTrue(lines[5].endswith("| " + plugin.MONO))
        self.assertEqual(lines[7], "---")

    def test_render_shows_the_collecting_row_without_history(self):
        out = plugin.render(make_state(0), NOW, python_path="/p/python3",
                            fetch_path="/r/fetch.py", history=[])
        self.assertIn("\n予測: データ収集中(あと約3時間) | " + plugin.MONO + "\n",
                      out)

    def test_render_omits_the_rows_for_a_stale_state(self):
        out = plugin.render(make_state(11), NOW, python_path="/p/python3",
                            fetch_path="/r/fetch.py", history=[])
        self.assertNotIn("予測", out)


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


def at(when, percent, resets_at=RESETS_AT, seven_day=None):
    return {"t": when, "fable": float(percent),
            "seven_day": None if seven_day is None else float(seven_day),
            "fable_resets_at": resets_at}


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
        return [at(start + timedelta(hours=h), h * 0.1, resets_at=self.resets,
                   seven_day=h * 0.05)
                for h in range(int(hours) + 1)]

    def test_short_history_falls_back_to_component_a(self):
        history = self.history(6)
        self.assertIsNone(plugin.activity_curve(history))
        self.assertIsNone(plugin.activity_curve(history, "seven_day"))
        rows = plugin.projection_rows(self.state(6), self.now, history)
        kind, value = plugin.project(history, plugin.parse_iso(self.resets),
                                     self.now)
        self.assertEqual(kind, "reset")
        self.assertEqual(rows[0], "%sリセット時点 ~%d%%"
                         % (plugin.pad_label("予測 Fable"), round(value)))

    def test_long_history_activates_the_curve_for_both_metrics(self):
        history = self.history(30)
        self.assertIsNotNone(plugin.activity_curve(history))
        self.assertIsNotNone(plugin.activity_curve(history, "seven_day"))
        rows = plugin.projection_rows(self.state(30), self.now, history)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertTrue(row.startswith("予測 "))
            self.assertNotIn("収集中", row)

    def test_two_hours_of_history_is_still_collecting(self):
        history = self.history(2)
        rows = plugin.projection_rows(self.state(2), self.now, history)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].startswith("予測: データ収集中("))


# --------------------------------------------------- 指標ごとのパイプライン

class SevenDayMetricTest(unittest.TestCase):
    """予測パイプラインは指標名だけを差し替えて再利用する。"""

    def test_slope_uses_the_requested_metric(self):
        points = [point(12, 6, seven_day=1), point(0, 12, seven_day=4)]
        self.assertAlmostEqual(plugin.pace_slope(points), 0.5, places=9)
        self.assertAlmostEqual(plugin.pace_slope(points, field="seven_day"),
                               0.25, places=9)

    def test_projection_uses_the_requested_metric(self):
        points = [point(12, 6, seven_day=1), point(0, 12, seven_day=4)]
        resets = NOW + timedelta(hours=10)
        self.assertEqual(plugin.project(points, resets, NOW),
                         ("reset", 17.0))
        self.assertEqual(plugin.project(points, resets, NOW,
                                        field="seven_day"),
                         ("reset", 6.5))

    def test_samples_without_a_seven_day_value_are_skipped(self):
        # API が seven_day に null を返した点は、その指標の窓には入らない。
        points = [point(12, 6, seven_day=1), point(6, 9), point(0, 12,
                                                                seven_day=4)]
        self.assertEqual(len(plugin.window_points(points, RESETS_AT)), 3)
        kept = plugin.window_points(points, RESETS_AT, "seven_day")
        self.assertEqual([p["seven_day"] for p in kept], [1.0, 4.0])

    def test_reset_is_detected_from_the_seven_day_drop(self):
        # fable は下がっていないが seven_day が下がっている = 週間側のリセット。
        pts = [point(12, 4, seven_day=80), point(6, 8, seven_day=2),
               point(0, 12, seven_day=5)]
        self.assertEqual(plugin.window_points(pts, RESETS_AT), pts)
        self.assertEqual(plugin.window_points(pts, RESETS_AT, "seven_day"),
                         pts[1:])

    def test_curve_is_built_from_the_metric_deltas(self):
        base = aligned(NOW) - timedelta(days=2)
        # fable は 1 時間目に、seven_day は 2 時間目にだけ伸びる。
        entries = [at(base, 0, seven_day=0),
                   at(base + timedelta(hours=1), 3, seven_day=0),
                   at(base + timedelta(hours=2), 3, seven_day=3),
                   at(base + timedelta(hours=30), 3, seven_day=3)]
        fable = plugin.activity_curve(entries)
        weekly = plugin.activity_curve(entries, "seven_day")
        self.assertAlmostEqual(fable[base.hour], 1.0, places=9)
        self.assertAlmostEqual(weekly[(base.hour + 1) % 24], 1.0, places=9)

    def test_curve_gates_apply_per_metric(self):
        base = aligned(NOW) - timedelta(days=2)
        # 24 時間以上あるが seven_day は一度も伸びていない -> 週間だけカーブ無し。
        entries = [at(base, 0, seven_day=5),
                   at(base + timedelta(hours=1), 3, seven_day=5),
                   at(base + timedelta(hours=30), 3, seven_day=5)]
        self.assertIsNotNone(plugin.activity_curve(entries))
        self.assertIsNone(plugin.activity_curve(entries, "seven_day"))

    def test_history_keeps_the_seven_day_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "history.jsonl")
            rows = [
                json.dumps({"t": (NOW - timedelta(hours=1)).isoformat(),
                            "fable": 11, "seven_day": 7,
                            "fable_resets_at": RESETS_AT}),
                json.dumps({"t": NOW.isoformat(), "fable": 12,
                            "seven_day": None,
                            "fable_resets_at": RESETS_AT}),
            ]
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(rows) + "\n")
            entries = plugin.read_history(path)
            self.assertEqual([e["seven_day"] for e in entries], [7.0, None])
            self.assertEqual(len(plugin.metric_entries(entries, "seven_day")), 1)


class LanguageConfigTest(unittest.TestCase):
    def test_explicit_language_wins_over_the_system_locale(self):
        state = {"locale_lang": "ja"}
        self.assertEqual(plugin.resolve_lang({"lang": "en"}, state), "en")
        self.assertEqual(plugin.resolve_lang({"lang": "ja"},
                                             {"locale_lang": "en"}), "ja")

    def test_auto_falls_back_to_the_state_locale(self):
        self.assertEqual(
            plugin.resolve_lang({"lang": "auto"}, {"locale_lang": "ja"}), "ja")
        self.assertEqual(
            plugin.resolve_lang({"lang": "auto"}, {"locale_lang": "en"}), "en")

    def test_missing_lang_key_is_auto(self):
        self.assertEqual(plugin.resolve_lang({}, {"locale_lang": "ja"}), "ja")

    def test_unknown_value_is_treated_as_auto(self):
        self.assertEqual(
            plugin.resolve_lang({"lang": "fr"}, {"locale_lang": "ja"}), "ja")

    def test_no_state_defaults_to_english(self):
        self.assertEqual(plugin.resolve_lang({"lang": "auto"}, None), "en")
        self.assertEqual(plugin.resolve_lang(None, None), "en")
        self.assertEqual(plugin.resolve_lang({}, {"locale_lang": "zz"}), "en")

    def test_case_and_padding_are_tolerated(self):
        self.assertEqual(plugin.resolve_lang({"lang": " EN "}, None), "en")
        self.assertEqual(plugin.resolve_lang({}, {"locale_lang": "JA"}), "ja")

    def test_read_config_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                plugin.read_config(os.path.join(tmp, "nope.json")), {})

    def test_read_config_tolerates_a_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{ not json")
            self.assertEqual(plugin.read_config(path), {})
            # A corrupt config must not change the language either.
            self.assertEqual(
                plugin.resolve_lang(plugin.read_config(path),
                                    {"locale_lang": "ja"}), "ja")

    def test_read_config_ignores_a_non_object_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('["en"]')
            self.assertEqual(plugin.read_config(path), {})

    def test_read_config_reads_a_hand_written_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"lang": "en"}\n')
            self.assertEqual(plugin.read_config(path), {"lang": "en"})

    def test_config_path_is_outside_the_cache_dir(self):
        # --purge deletes the cache dir; settings must survive it.
        self.assertNotIn(plugin.CACHE_DIR, plugin.CONFIG_PATH)
        self.assertTrue(plugin.CONFIG_PATH.endswith(
            os.path.join(".config", "fable-meter", "config.json")))


class EnglishRenderTest(unittest.TestCase):
    def history(self):
        return [point(12, 6, seven_day=4), point(0, 12, seven_day=8)]

    def render(self, state=None, **kwargs):
        kwargs.setdefault("python_path", "/p/python3")
        kwargs.setdefault("fetch_path", "/r/fetch.py")
        kwargs.setdefault("history", self.history())
        return plugin.render(make_state(3) if state is None else state, NOW,
                             lang="en", **kwargs)

    def test_labels_and_actions_are_english(self):
        out = self.render()
        self.assertIn("Weekly (all models)", out)
        self.assertIn("Session (5h)", out)
        self.assertIn("resets ", out)
        self.assertNotIn("週間", out)
        self.assertNotIn("リセット", out)
        self.assertNotIn("取得", out)
        self.assertIn("Refresh | bash=/p/python3 param1=/r/fetch.py", out)
        self.assertIn("Open usage page | href=", out)
        self.assertIn("Open log | bash=/usr/bin/open", out)

    def test_fetched_row_is_english(self):
        rows = [ln for ln in self.render().split("\n")
                if ln.startswith("fetched: ")]
        self.assertEqual(rows, ["fetched: %s (3m ago)"
                                % (NOW - timedelta(minutes=3)).astimezone()
                                .strftime("%H:%M:%S")])

    def test_durations_are_compact_english(self):
        self.assertEqual(plugin.fmt_duration(0, "en"), "0m")
        self.assertEqual(plugin.fmt_duration(90, "en"), "1m")
        self.assertEqual(plugin.fmt_duration(3600 * 3 + 60 * 21, "en"), "3h 21m")
        self.assertEqual(plugin.fmt_duration(86400 * 3 + 3600 * 19, "en"),
                         "3d 19h")

    def test_reset_line_is_english(self):
        self.assertEqual(
            plugin.fmt_reset("2026-09-04T23:05:00+00:00", NOW, "en"),
            "resets %s (6d 2h left)"
            % plugin.parse_iso("2026-09-04T23:05:00+00:00").astimezone()
            .strftime("%b %-d %H:%M"))
        # Inside 24h the date is dropped in both languages.
        self.assertTrue(
            plugin.fmt_reset("2026-08-29T22:00:00+00:00", NOW, "en")
            .startswith("resets "))
        self.assertTrue(
            plugin.fmt_reset("2026-08-29T20:00:00+00:00", NOW, "en")
            .startswith("resets "))

    def test_forecast_rows_are_english(self):
        rows = plugin.projection_rows(make_state(0), NOW, self.history(), "en")
        self.assertEqual(rows, ["Forecast Fable       ~81% at reset",
                                "Forecast Weekly      ~54% at reset"])

    def test_forecast_crossing_row_is_english(self):
        history = [point(10, 10, seven_day=10), point(0, 50, seven_day=50)]
        rows = plugin.projection_rows(make_state(0, fable=50), NOW, history,
                                      "en")
        stamp = (NOW + timedelta(hours=12.5)).astimezone().strftime(
            "%b %-d, %-I%p").replace("AM", "am").replace("PM", "pm")
        self.assertEqual(rows[0], "Forecast Fable       hits 100%% ~%s" % stamp)

    def test_collecting_rows_are_english(self):
        self.assertEqual(plugin.collecting_label([], NOW, "en"), "~3h left")
        self.assertEqual(
            plugin.collecting_label([point(175 / 60.0, 1)], NOW, "en"),
            "~10m left")
        self.assertEqual(plugin.projection_rows(make_state(0), NOW, [], "en"),
                         ["Forecast: collecting data (~3h left)"])
        self.assertEqual(
            plugin.projection_rows(make_state(0), NOW,
                                   [point(12, 6), point(0, 12)], "en"),
            ["Forecast Fable       ~81% at reset",
             "Forecast Weekly      collecting data (~3h left)"])

    def test_english_labels_still_line_up(self):
        # pad_label is east-asian-width aware; English labels are all
        # single-cell but longer, so they get their own column width.
        self.assertEqual(plugin.display_width(
            plugin.pad_label("Weekly (all models)", lang="en")),
            plugin.LABEL_WIDTH_EN)
        self.assertEqual(plugin.display_width(
            plugin.pad_label("Session (5h)", lang="en")),
            plugin.LABEL_WIDTH_EN)
        rows = plugin.projection_rows(make_state(0), NOW, self.history(), "en")
        self.assertEqual(
            {plugin.display_width(r.split("~")[0]) for r in rows},
            {plugin.LABEL_WIDTH_EN})
        # The percent column starts at the same cell on all three limit rows.
        limit = [ln.split(" | ")[0].replace(plugin.ANSI_PRIMARY_DARK, "")
                 for ln in self.render(make_state(0), dark=True).split("\n")
                 if "resets" in ln]
        self.assertEqual(len(limit), 3)
        self.assertEqual({row.index("%") for row in limit},
                         {plugin.LABEL_WIDTH_EN + 4})

    def test_no_data_and_error_rows_are_english(self):
        state = make_state(0)
        state["data"] = None
        self.assertIn("\nNo data yet\n", self.render(state))
        row = [ln for ln in self.render(
            make_state(3, ok=False, error="token_expired")).split("\n")
            if "token_expired" in ln][0]
        self.assertTrue(row.startswith(plugin.ANSI_ERROR + "Error: "))
        self.assertIn("Error: state.json is missing or unreadable",
                      plugin.render(None, NOW, lang="en"))


class ForecastRowColourTest(unittest.TestCase):
    def history(self):
        return [point(12, 6, seven_day=4), point(0, 12, seven_day=8)]

    def forecast_rows(self, out):
        return [ln for ln in out.split("\n")
                if "予測" in ln or "Forecast" in ln]

    def test_forecast_rows_use_the_same_near_white_as_the_limit_rows(self):
        for lang in ("ja", "en"):
            out = plugin.render(make_state(0), NOW, history=self.history(),
                                dark=True, lang=lang)
            rows = self.forecast_rows(out)
            self.assertEqual(len(rows), 2, lang)
            for row in rows:
                self.assertTrue(row.startswith(plugin.ANSI_PRIMARY_DARK), row)
                self.assertIn("ansi=true", row)
                self.assertIn("font=Menlo size=12", row)
                # Still an information row: no action, no color= parameter.
                self.assertNotIn("color=", row)
                self.assertNotIn("bash=", row)
                self.assertNotIn("href=", row)

    def test_all_five_state_rows_are_coloured_together(self):
        out = plugin.render(make_state(0), NOW, history=self.history(),
                            dark=True)
        coloured = [ln for ln in out.split("\n")
                    if ln.startswith(plugin.ANSI_PRIMARY_DARK)]
        self.assertEqual(len(coloured), 5)

    def test_forecast_rows_stay_uncoloured_in_light_appearance(self):
        out = plugin.render(make_state(0), NOW, history=self.history(),
                            dark=False)
        rows = self.forecast_rows(out)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertNotIn(plugin.ESC, row)
            self.assertNotIn("ansi=true", row)
            self.assertIn("font=Menlo size=12", row)


if __name__ == "__main__":
    unittest.main()


class ScopedRowsTest(unittest.TestCase):
    """`data.scoped` を API の順に 1 行ずつ。Fable 1 枠のときは従来と同一。"""

    def test_single_fable_matches_the_legacy_fixed_row(self):
        # 従来の実装 = scoped を見ずに data.fable を 1 行出す挙動。
        # scoped を落とした state はそのフォールバックを通るので、
        # 「scoped が Fable 1 件だけ」の出力と 1 バイトも違ってはいけない。
        state = make_state()
        legacy = json.loads(json.dumps(state))
        del legacy["data"]["scoped"]
        self.assertEqual(plugin.render(state, NOW, dark=False),
                         plugin.render(legacy, NOW, dark=False))
        self.assertEqual(plugin.render(state, NOW, dark=True, lang="en"),
                         plugin.render(legacy, NOW, dark=True, lang="en"))

    def test_two_scoped_entries_render_two_rows_in_api_order(self):
        state = make_state()
        state["data"]["scoped"].append(
            {"name": "Opus 4.5", "percent": 31,
             "resets_at": "2026-09-04T14:59:59+00:00", "severity": "normal"})
        out = plugin.render(state, NOW, dark=False)
        rows = [line for line in out.split("\n") if plugin.MONO in line]
        self.assertTrue(rows[0].startswith(plugin.pad_label("Fable")))
        self.assertTrue(rows[1].startswith(plugin.pad_label("Opus 4.5")))
        self.assertIn(" 31%", rows[1])
        # 週間 / セッションはスコープ行の後ろのまま。
        self.assertTrue(rows[2].startswith(plugin.pad_label("週間(全モデル)")))
        self.assertTrue(rows[3].startswith(plugin.pad_label("セッション(5h)")))

    def test_extra_scoped_entry_does_not_change_the_title(self):
        state = make_state()
        state["data"]["scoped"].append(
            {"name": "Opus 4.5", "percent": 31,
             "resets_at": "2026-09-04T14:59:59+00:00", "severity": "normal"})
        self.assertEqual(plugin.title_line(state, NOW), "F12% W9% S6%")

    def test_scoped_name_is_not_translated(self):
        state = make_state()
        state["data"]["scoped"] = [
            {"name": "Fable 5.1", "percent": 12,
             "resets_at": "2026-09-04T14:59:59+00:00", "severity": "normal"}]
        for lang in ("ja", "en"):
            self.assertIn("Fable 5.1", plugin.render(state, NOW, dark=False,
                                                     lang=lang))

    def test_no_forecast_row_for_arbitrary_scoped_entries(self):
        # history.jsonl は fable と seven_day しか記録しないので、予測は 2 本のまま。
        self.assertEqual([field for field, _ in plugin.PROJECTION_METRICS],
                         ["fable", "seven_day"])

    def test_broken_scoped_falls_back_to_the_fable_row(self):
        for broken in ([], "nope", [None, {"name": 5}]):
            state = make_state()
            state["data"]["scoped"] = broken
            rows = plugin.limit_rows(state["data"])
            self.assertEqual(rows, [("Fable", state["data"]["fable"])])


class TruncateLabelTest(unittest.TestCase):
    def test_short_labels_are_untouched(self):
        self.assertEqual(plugin.truncate_label("Fable", 16), "Fable")
        self.assertEqual(plugin.truncate_label("Weekly (all models)", 21),
                         "Weekly (all models)")

    def test_exact_width_is_untouched(self):
        self.assertEqual(plugin.truncate_label("a" * 16, 16), "a" * 16)

    def test_long_label_is_cut_with_an_ellipsis(self):
        cut = plugin.truncate_label("Fable 5.1 Extended Thinking", 16)
        self.assertEqual(cut, "Fable 5.1 Exten…")
        self.assertEqual(plugin.display_width(cut), 16)

    def test_full_width_label_never_overflows(self):
        cut = plugin.truncate_label("あ" * 20, 16)
        self.assertLessEqual(plugin.display_width(cut), 16)
        self.assertTrue(cut.endswith("…"))

    def test_pad_label_truncates_long_names(self):
        padded = plugin.pad_label("Fable 5.1 Extended Thinking", lang="ja")
        self.assertEqual(plugin.display_width(padded), 16)
        padded_en = plugin.pad_label("Fable 5.1 Extended Thinking", lang="en")
        self.assertEqual(plugin.display_width(padded_en), 21)

    def test_long_scoped_name_keeps_the_columns_aligned(self):
        state = make_state()
        state["data"]["scoped"] = [
            {"name": "Fable 5.1 Extended Thinking", "percent": 12,
             "resets_at": "2026-09-04T14:59:59+00:00", "severity": "normal"}]
        out = plugin.render(state, NOW, dark=False)
        rows = [line for line in out.split("\n") if plugin.MONO in line]
        self.assertTrue(rows[0].startswith("Fable 5.1 Exten… "))
        # 3 行とも同じ桁で % が始まる = 長い名前でも桁が崩れない。
        # "%s %3s%%" なので、"%" の手前から 3 文字落とすとラベル + 空白 1 個。
        starts = [plugin.display_width(row.split("%")[0][:-3])
                  for row in rows[:3]]
        self.assertEqual(starts, [starts[0]] * 3)
