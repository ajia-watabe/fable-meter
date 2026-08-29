import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fetch  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
