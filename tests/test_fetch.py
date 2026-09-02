import copy
import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fetch  # noqa: E402

NOW = datetime(2026, 8, 29, 21, 5, 0, tzinfo=timezone.utc)
RESETS_AT = "2026-09-04T14:59:59+00:00"

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "usage_response.json")


def load_fixture():
    with open(FIXTURE, "r", encoding="utf-8") as fh:
        return json.load(fh)


class ParseUsageTest(unittest.TestCase):
    def test_extracts_all_three_windows(self):
        data = fetch.parse_usage(load_fixture())
        self.assertEqual(data["fable"]["percent"], 12)
        self.assertEqual(data["fable"]["resets_at"], "2026-09-04T14:59:59+00:00")
        self.assertEqual(data["fable"]["severity"], "normal")
        self.assertEqual(data["seven_day"]["percent"], 9)
        self.assertEqual(data["five_hour"]["percent"], 6)
        self.assertEqual([s["name"] for s in data["scoped"]], ["Fable"])

    def test_plan_passthrough(self):
        data = fetch.parse_usage(load_fixture(), plan="max")
        self.assertEqual(data["plan"], "max")

    def test_case_insensitive_fable(self):
        payload = load_fixture()
        payload["limits"][2]["scope"]["model"]["display_name"] = "fable"
        self.assertEqual(fetch.parse_usage(payload)["fable"]["percent"], 12)

    def test_fallback_to_toplevel_when_limits_lack_kinds(self):
        payload = load_fixture()
        payload["limits"] = [payload["limits"][2]]
        data = fetch.parse_usage(payload)
        self.assertEqual(data["five_hour"]["percent"], 6.0)
        self.assertEqual(data["seven_day"]["percent"], 9.0)

    def test_missing_fable_raises(self):
        payload = load_fixture()
        payload["limits"] = [l for l in payload["limits"]
                             if l["kind"] != "weekly_scoped"]
        with self.assertRaises(fetch.FetchError) as ctx:
            fetch.parse_usage(payload)
        self.assertEqual(ctx.exception.code, "fable_not_found")

    def test_other_scoped_model_is_not_fable(self):
        payload = load_fixture()
        payload["limits"][2]["scope"]["model"]["display_name"] = "Opus"
        with self.assertRaises(fetch.FetchError) as ctx:
            fetch.parse_usage(payload)
        self.assertEqual(ctx.exception.code, "fable_not_found")

    def test_missing_limits_raises_schema_error(self):
        payload = load_fixture()
        del payload["limits"]
        with self.assertRaises(fetch.FetchError) as ctx:
            fetch.parse_usage(payload)
        self.assertEqual(ctx.exception.code, "schema_error")

    def test_limits_not_a_list_raises_schema_error(self):
        payload = load_fixture()
        payload["limits"] = {"kind": "session"}
        with self.assertRaises(fetch.FetchError) as ctx:
            fetch.parse_usage(payload)
        self.assertEqual(ctx.exception.code, "schema_error")

    def test_non_numeric_percent_raises_schema_error(self):
        payload = load_fixture()
        payload["limits"][2]["percent"] = "12"
        with self.assertRaises(fetch.FetchError) as ctx:
            fetch.parse_usage(payload)
        self.assertEqual(ctx.exception.code, "schema_error")

    def test_null_percent_raises_schema_error(self):
        payload = load_fixture()
        payload["limits"][0]["percent"] = None
        with self.assertRaises(fetch.FetchError) as ctx:
            fetch.parse_usage(payload)
        self.assertEqual(ctx.exception.code, "schema_error")


class StateTest(unittest.TestCase):
    def test_error_state_preserves_previous_data(self):
        good = fetch.build_success_state(fetch.parse_usage(load_fixture()))
        bad = fetch.build_error_state("token_expired", previous=copy.deepcopy(good))
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"], "token_expired")
        self.assertIsNotNone(bad["error_at"])
        self.assertEqual(bad["data"], good["data"])
        self.assertEqual(bad["fetched_at"], good["fetched_at"])

    def test_error_state_without_previous(self):
        bad = fetch.build_error_state("keychain_error", previous=None)
        self.assertIsNone(bad["data"])
        self.assertIsNone(bad["fetched_at"])
        self.assertEqual(bad["schema"], 1)

    def test_write_state_is_atomic_and_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "state.json")
            state = fetch.build_success_state(fetch.parse_usage(load_fixture()))
            fetch.write_state(state, path)
            self.assertFalse(os.path.exists(path + ".tmp"))
            self.assertEqual(fetch.load_state(path), state)

    def test_load_state_of_broken_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            self.assertIsNone(fetch.load_state(path))
            self.assertIsNone(fetch.load_state(os.path.join(tmp, "nope.json")))


class TokenHelpersTest(unittest.TestCase):
    def test_token_expiry(self):
        self.assertTrue(fetch.token_is_expired(1000, now_ms=2000))
        self.assertTrue(fetch.token_is_expired(2000, now_ms=2000))
        self.assertFalse(fetch.token_is_expired(3000, now_ms=2000))
        self.assertFalse(fetch.token_is_expired(None, now_ms=2000))
        self.assertFalse(fetch.token_is_expired("junk", now_ms=2000))

    def test_waketime_parsing(self):
        out = "{ sec = 1756450000, usec = 123456 } Fri Aug 29 21:00:00 2026\n"
        self.assertEqual(fetch.waketime_seconds(out), 1756450000)
        self.assertIsNone(fetch.waketime_seconds(""))
        self.assertIsNone(fetch.waketime_seconds("garbage"))

    def test_classify_http(self):
        self.assertEqual(fetch._classify_http(429, "")[0], "rate_limited")
        self.assertEqual(fetch._classify_http(500, "boom")[0], "http_500")
        code, detail = fetch._classify_http(403, '{"error":{"type":"oauth_bad"}}')
        self.assertEqual(code, "auth_error")
        self.assertEqual(detail, "oauth_bad")


class HistoryTest(unittest.TestCase):
    def make_entry(self, days_ago, percent=10):
        stamp = NOW - timedelta(days=days_ago)
        return json.dumps({"t": stamp.isoformat(), "fable": percent,
                           "seven_day": 5, "fable_resets_at": RESETS_AT})

    def test_history_entry_shape(self):
        data = fetch.parse_usage(load_fixture())
        entry = fetch.history_entry(data, now=NOW)
        self.assertEqual(entry["fable"], 12)
        self.assertEqual(entry["seven_day"], 9)
        self.assertEqual(entry["fable_resets_at"], RESETS_AT)
        self.assertTrue(entry["t"].endswith("+00:00"))
        self.assertEqual(fetch.parse_iso(entry["t"]), NOW)

    def test_history_entry_requires_numeric_fable(self):
        self.assertIsNone(fetch.history_entry(None))
        self.assertIsNone(fetch.history_entry({"fable": {"percent": None}}))
        self.assertIsNone(fetch.history_entry({"fable": {"percent": True}}))

    def test_history_entry_tolerates_missing_seven_day(self):
        entry = fetch.history_entry({"fable": {"percent": 3, "resets_at": None},
                                     "seven_day": None}, now=NOW)
        self.assertEqual(entry["fable"], 3)
        self.assertIsNone(entry["seven_day"])

    def test_prune_drops_entries_older_than_8_days(self):
        lines = [self.make_entry(9), self.make_entry(8.5), self.make_entry(7),
                 self.make_entry(0)]
        kept = fetch.prune_history_lines(lines, now=NOW)
        self.assertEqual(len(kept), 2)
        self.assertEqual([json.loads(l)["t"] for l in kept],
                         [json.loads(lines[2])["t"], json.loads(lines[3])["t"]])

    def test_prune_drops_garbage_and_blank_lines(self):
        lines = ["", "   ", "{not json", "[1,2]", '{"fable": 1}',
                 self.make_entry(1)]
        kept = fetch.prune_history_lines(lines, now=NOW)
        self.assertEqual(len(kept), 1)

    def test_append_history_appends_and_prunes_with_0600(self):
        data = fetch.parse_usage(load_fixture())
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "history.jsonl")
            os.makedirs(os.path.dirname(path), mode=0o700)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.make_entry(9) + "\n")
                fh.write(self.make_entry(1) + "\n")
            fetch.append_history(data, now=NOW, path=path)
            with open(path, "r", encoding="utf-8") as fh:
                lines = fh.read().splitlines()
            self.assertEqual(len(lines), 2)  # the 9-day-old line was pruned
            self.assertEqual(json.loads(lines[-1])["fable"], 12)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_append_history_creates_file_0600(self):
        data = fetch.parse_usage(load_fixture())
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "history.jsonl")
            fetch.append_history(data, now=NOW, path=path)
            fetch.append_history(data, now=NOW, path=path)
            with open(path, "r", encoding="utf-8") as fh:
                self.assertEqual(len(fh.read().splitlines()), 2)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


class NotifyBandTest(unittest.TestCase):
    def state(self, percent, band, resets_at=RESETS_AT):
        return {"data": {"fable": {"percent": percent, "resets_at": resets_at}},
                "last_notified_band": band}

    def test_band_for_percent(self):
        self.assertEqual(fetch.band_for_percent(0), 0)
        self.assertEqual(fetch.band_for_percent(79.9), 0)
        self.assertEqual(fetch.band_for_percent(80), 1)
        self.assertEqual(fetch.band_for_percent(94), 1)
        self.assertEqual(fetch.band_for_percent(95), 2)
        self.assertEqual(fetch.band_for_percent(120), 2)
        self.assertEqual(fetch.band_for_percent(None), 0)
        self.assertEqual(fetch.band_for_percent(True), 0)

    def test_up_cross_fires_once(self):
        previous = self.state(70, 0)
        band, percent = fetch.evaluate_band(
            previous, {"percent": 81, "resets_at": RESETS_AT})
        self.assertEqual((band, percent), (1, 81))
        # same window, still in band 1 -> no second notification
        previous = self.state(81, band)
        band, percent = fetch.evaluate_band(
            previous, {"percent": 90, "resets_at": RESETS_AT})
        self.assertEqual((band, percent), (1, None))

    def test_second_band_fires_separately(self):
        previous = self.state(90, 1)
        band, percent = fetch.evaluate_band(
            previous, {"percent": 96.4, "resets_at": RESETS_AT})
        self.assertEqual((band, percent), (2, 96))
        previous = self.state(96, 2)
        self.assertEqual(fetch.evaluate_band(
            previous, {"percent": 99, "resets_at": RESETS_AT}), (2, None))

    def test_percent_drop_clears_band_and_can_refire(self):
        previous = self.state(96, 2)
        band, percent = fetch.evaluate_band(
            previous, {"percent": 4, "resets_at": RESETS_AT})
        self.assertEqual((band, percent), (0, None))
        band, percent = fetch.evaluate_band(
            self.state(96, 2), {"percent": 85, "resets_at": RESETS_AT})
        self.assertEqual((band, percent), (1, 85))

    def test_resets_at_moving_forward_clears_band(self):
        previous = self.state(90, 1, resets_at="2026-09-04T14:59:59+00:00")
        band, percent = fetch.evaluate_band(
            previous, {"percent": 90, "resets_at": "2026-09-11T14:59:59+00:00"})
        self.assertEqual((band, percent), (1, 90))

    def test_sub_second_resets_at_jitter_is_not_a_reset(self):
        previous = self.state(90, 1, resets_at="2026-09-04T14:59:59.051929+00:00")
        band, percent = fetch.evaluate_band(
            previous, {"percent": 90, "resets_at": "2026-09-04T14:59:59.487294+00:00"})
        self.assertEqual((band, percent), (1, None))

    def test_no_previous_state_notifies_from_zero(self):
        self.assertEqual(
            fetch.evaluate_band(None, {"percent": 83, "resets_at": RESETS_AT}),
            (1, 83))
        self.assertEqual(
            fetch.evaluate_band(None, {"percent": 12, "resets_at": RESETS_AT}),
            (0, None))

    def test_corrupt_band_is_treated_as_zero(self):
        self.assertEqual(fetch.stored_band({"last_notified_band": "2"}), 0)
        self.assertEqual(fetch.stored_band({"last_notified_band": True}), 0)
        self.assertEqual(fetch.stored_band({"last_notified_band": 9}), 0)
        self.assertEqual(fetch.stored_band({"last_notified_band": 2}), 2)
        self.assertEqual(fetch.stored_band(None), 0)

    def test_window_reset_needs_comparable_values(self):
        self.assertFalse(fetch.window_reset(None, {"percent": 5}))
        self.assertFalse(fetch.window_reset({"data": {}}, {"percent": 5}))

    def test_state_carries_band(self):
        good = fetch.build_success_state(fetch.parse_usage(load_fixture()),
                                         last_notified_band=2)
        self.assertEqual(good["last_notified_band"], 2)
        bad = fetch.build_error_state("network_error", previous=good)
        self.assertEqual(bad["last_notified_band"], 2)
        self.assertEqual(
            fetch.build_error_state("network_error", None)["last_notified_band"], 0)

    def test_notification_text_only_interpolates_integers(self):
        seen = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            return None

        original = fetch.subprocess.run
        fetch.subprocess.run = fake_run
        try:
            self.assertTrue(fetch.notify(1, 83))
        finally:
            fetch.subprocess.run = original
        self.assertEqual(seen["argv"][0], "/usr/bin/osascript")
        self.assertEqual(seen["argv"][1], "-e")
        self.assertEqual(
            seen["argv"][2],
            'display notification "Fable が80%を超えました(現在 83%)" '
            'with title "fable-meter"')

    def test_notify_ignores_unknown_band(self):
        self.assertFalse(fetch.notify(0, 5))


class ConfigTest(unittest.TestCase):
    def test_config_path_is_outside_the_cache_dir(self):
        # uninstall.sh --purge removes the cache dir; settings must survive.
        self.assertNotIn(fetch.CACHE_DIR, fetch.CONFIG_PATH)
        self.assertTrue(fetch.CONFIG_PATH.endswith(
            os.path.join(".config", "fable-meter", "config.json")))

    def test_read_config_missing_and_corrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            self.assertEqual(fetch.read_config(path), {})
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{oops")
            self.assertEqual(fetch.read_config(path), {})
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("42")
            self.assertEqual(fetch.read_config(path), {})

    def test_ensure_config_creates_a_0600_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = os.path.join(tmp, "fable-meter")
            path = os.path.join(directory, "config.json")
            self.assertTrue(fetch.ensure_config(path))
            with open(path, "r", encoding="utf-8") as fh:
                self.assertEqual(json.load(fh), {"lang": "auto"})
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(directory).st_mode), 0o700)

    def test_ensure_config_never_overwrites_a_hand_edited_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"lang": "en"}\n')
            self.assertFalse(fetch.ensure_config(path))
            self.assertEqual(fetch.read_config(path), {"lang": "en"})


class LocaleTest(unittest.TestCase):
    def test_lang_from_locale(self):
        self.assertEqual(fetch._lang_from_locale("ja_JP"), "ja")
        self.assertEqual(fetch._lang_from_locale("ja-JP.UTF-8\n"), "ja")
        self.assertEqual(fetch._lang_from_locale("en_US"), "en")
        self.assertEqual(fetch._lang_from_locale("fr_FR"), "en")
        self.assertIsNone(fetch._lang_from_locale(""))
        self.assertIsNone(fetch._lang_from_locale(None))

    def _with_defaults_output(self, out, env):
        class Result(object):
            stdout = out

        def fake_run(argv, **kwargs):
            self.assertEqual(argv, ["/usr/bin/defaults", "read", "-g",
                                    "AppleLocale"])
            if out is None:
                raise OSError("boom")
            return Result()

        original = fetch.subprocess.run
        fetch.subprocess.run = fake_run
        try:
            return fetch.system_locale_lang(env)
        finally:
            fetch.subprocess.run = original

    def test_system_locale_comes_from_defaults(self):
        self.assertEqual(self._with_defaults_output("ja_JP\n", {}), "ja")
        self.assertEqual(self._with_defaults_output("en_US\n", {}), "en")

    def test_falls_back_to_the_environment(self):
        self.assertEqual(
            self._with_defaults_output("", {"LANG": "ja_JP.UTF-8"}), "ja")
        self.assertEqual(
            self._with_defaults_output(None, {"LC_ALL": "ja_JP.UTF-8"}), "ja")
        self.assertEqual(
            self._with_defaults_output("", {"LANG": "de_DE.UTF-8"}), "en")

    def test_defaults_to_english(self):
        self.assertEqual(self._with_defaults_output("", {}), "en")

    def test_resolve_lang_precedence(self):
        self.assertEqual(fetch.resolve_lang({"lang": "en"}, "ja"), "en")
        self.assertEqual(fetch.resolve_lang({"lang": "ja"}, "en"), "ja")
        self.assertEqual(fetch.resolve_lang({"lang": "auto"}, "ja"), "ja")
        self.assertEqual(fetch.resolve_lang({}, "ja"), "ja")
        self.assertEqual(fetch.resolve_lang({"lang": "fr"}, "ja"), "ja")
        self.assertEqual(fetch.resolve_lang({"lang": "auto"}, None), "en")
        self.assertEqual(fetch.resolve_lang(None, None), "en")

    def test_state_carries_the_resolved_locale(self):
        good = fetch.build_success_state({"fable": {}}, now=NOW,
                                         locale_lang="ja")
        self.assertEqual(good["locale_lang"], "ja")
        self.assertIsNone(fetch.build_success_state({}, now=NOW)["locale_lang"])
        self.assertIsNone(
            fetch.build_success_state({}, now=NOW,
                                      locale_lang="fr")["locale_lang"])

    def test_error_state_keeps_the_previous_locale(self):
        good = fetch.build_success_state({}, now=NOW, locale_lang="ja")
        bad = fetch.build_error_state("network_error", previous=good)
        self.assertEqual(bad["locale_lang"], "ja")
        fresh = fetch.build_error_state("network_error", previous=good,
                                        locale_lang="en")
        self.assertEqual(fresh["locale_lang"], "en")
        self.assertIsNone(
            fetch.build_error_state("network_error", None)["locale_lang"])


class NotificationLanguageTest(unittest.TestCase):
    def test_english_notification_text(self):
        self.assertEqual(fetch.notification_text(1, 83, "en"),
                         "Fable exceeded 80% (now 83%)")
        self.assertEqual(fetch.notification_text(2, 96, "en"),
                         "Fable exceeded 95% (now 96%)")

    def test_japanese_notification_text(self):
        self.assertEqual(fetch.notification_text(1, 83, "ja"),
                         "Fable が80%を超えました(現在 83%)")

    def test_unknown_language_falls_back_to_english(self):
        self.assertEqual(fetch.notification_text(1, 83, "fr"),
                         "Fable exceeded 80% (now 83%)")

    def test_unknown_band_has_no_text(self):
        self.assertIsNone(fetch.notification_text(0, 5, "en"))

    def test_notify_uses_the_requested_language(self):
        seen = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            return None

        original = fetch.subprocess.run
        fetch.subprocess.run = fake_run
        try:
            self.assertTrue(fetch.notify(2, 96, lang="en"))
        finally:
            fetch.subprocess.run = original
        self.assertEqual(
            seen["argv"][2],
            'display notification "Fable exceeded 95% (now 96%)" '
            'with title "fable-meter"')


if __name__ == "__main__":
    unittest.main()


def scoped_limit(name, percent, is_active=False,
                 resets_at="2026-09-04T14:59:59+00:00"):
    return {"kind": "weekly_scoped", "group": "weekly", "percent": percent,
            "severity": "normal", "resets_at": resets_at,
            "scope": {"model": {"id": None, "display_name": name},
                      "surface": None},
            "is_active": is_active}


class SelectFableTest(unittest.TestCase):
    """§4 の選択の梯子: 完全一致 > 前方一致 > is_active > percent 最大。"""

    def test_exact_match_wins_over_prefix(self):
        payload = load_fixture()
        payload["limits"] = [
            scoped_limit("Fable 5.1", 90, is_active=True),
            scoped_limit("Fable", 12),
        ]
        data = fetch.parse_usage(payload)
        self.assertEqual(data["fable"]["name"], "Fable")
        self.assertEqual(data["fable"]["percent"], 12)

    def test_exact_match_is_case_insensitive(self):
        payload = load_fixture()
        payload["limits"] = [scoped_limit("FABLE", 12)]
        self.assertEqual(fetch.parse_usage(payload)["fable"]["name"], "FABLE")

    def test_single_prefix_match_is_used(self):
        payload = load_fixture()
        payload["limits"] = [scoped_limit("Opus", 3), scoped_limit("Fable 5.1", 42)]
        data = fetch.parse_usage(payload)
        self.assertEqual(data["fable"]["name"], "Fable 5.1")
        self.assertEqual(data["fable"]["percent"], 42)

    def test_prefix_match_is_case_insensitive(self):
        payload = load_fixture()
        payload["limits"] = [scoped_limit("fable 6", 7)]
        self.assertEqual(fetch.parse_usage(payload)["fable"]["name"], "fable 6")

    def test_unique_active_beats_higher_percent(self):
        payload = load_fixture()
        payload["limits"] = [
            scoped_limit("Fable 5", 80, is_active=False),
            scoped_limit("Fable 6", 20, is_active=True),
        ]
        data = fetch.parse_usage(payload)
        self.assertEqual(data["fable"]["name"], "Fable 6")

    def test_highest_percent_when_several_active(self):
        payload = load_fixture()
        payload["limits"] = [
            scoped_limit("Fable 5", 20, is_active=True),
            scoped_limit("Fable 6", 55, is_active=True),
        ]
        self.assertEqual(fetch.parse_usage(payload)["fable"]["name"], "Fable 6")

    def test_highest_percent_when_none_active(self):
        payload = load_fixture()
        payload["limits"] = [
            scoped_limit("Fable 5", 20),
            scoped_limit("Fable 6", 55),
        ]
        self.assertEqual(fetch.parse_usage(payload)["fable"]["name"], "Fable 6")

    def test_percent_tie_keeps_api_order(self):
        payload = load_fixture()
        payload["limits"] = [
            scoped_limit("Fable 5", 30),
            scoped_limit("Fable 6", 30),
        ]
        self.assertEqual(fetch.parse_usage(payload)["fable"]["name"], "Fable 5")

    def test_no_match_raises_fable_not_found(self):
        payload = load_fixture()
        payload["limits"] = [scoped_limit("Opus", 3), scoped_limit("Sonnet", 4)]
        with self.assertRaises(fetch.FetchError) as ctx:
            fetch.parse_usage(payload)
        self.assertEqual(ctx.exception.code, "fable_not_found")

    def test_substring_but_not_prefix_does_not_match(self):
        payload = load_fixture()
        payload["limits"] = [scoped_limit("Claude Fable", 3)]
        with self.assertRaises(fetch.FetchError) as ctx:
            fetch.parse_usage(payload)
        self.assertEqual(ctx.exception.code, "fable_not_found")

    def test_state_records_the_chosen_name(self):
        payload = load_fixture()
        payload["limits"] = [scoped_limit("Fable 5.1", 42, is_active=True)]
        state = fetch.build_success_state(fetch.parse_usage(payload), now=NOW)
        self.assertEqual(state["data"]["fable"]["name"], "Fable 5.1")

    def test_exact_match_also_records_its_name(self):
        state = fetch.build_success_state(fetch.parse_usage(load_fixture()), now=NOW)
        self.assertEqual(state["data"]["fable"]["name"], "Fable")

    def test_all_scoped_entries_are_kept(self):
        payload = load_fixture()
        payload["limits"] = [
            scoped_limit("Fable", 12),
            scoped_limit("Opus", 3),
        ]
        data = fetch.parse_usage(payload)
        self.assertEqual([s["name"] for s in data["scoped"]], ["Fable", "Opus"])
        # is_active は選択にだけ使い、state には載せない。
        self.assertEqual(sorted(data["scoped"][0]),
                         ["name", "percent", "resets_at", "severity"])

    def test_select_fable_tolerates_missing_actives(self):
        scoped = [{"name": "Fable", "percent": 12, "resets_at": None,
                   "severity": None}]
        self.assertIs(fetch.select_fable(scoped), scoped[0])
        self.assertIsNone(fetch.select_fable([]))
        self.assertIsNone(fetch.select_fable(None))
