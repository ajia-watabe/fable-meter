#!/usr/bin/env python3
"""fable-meter fetch layer.

Reads the Claude Code OAuth token from the macOS Keychain (in-process only),
calls GET https://api.anthropic.com/api/oauth/usage, parses limits[] and writes
~/.cache/fable-meter/state.json atomically.

SECURITY: the token is never printed, logged, written to disk, passed as an
argument, or embedded in an exception message.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

SCHEMA = 1
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"
# Absolute paths only: never resolve helper binaries through $PATH.
SECURITY_BIN = "/usr/bin/security"
SYSCTL_BIN = "/usr/sbin/sysctl"
OSASCRIPT_BIN = "/usr/bin/osascript"
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "fable-meter")
STATE_PATH = os.path.join(CACHE_DIR, "state.json")
LOG_PATH = os.path.join(CACHE_DIR, "fetch.log")
HISTORY_PATH = os.path.join(CACHE_DIR, "history.jsonl")
LOG_MAX_BYTES = 200 * 1024
HTTP_TIMEOUT = 15
WAKE_SKIP_SECONDS = 60
HISTORY_MAX_DAYS = 8
# 閾値通知のバンド: 0 = 平常, 1 = 80% 以上, 2 = 95% 以上。
NOTIFY_THRESHOLDS = ((2, 95), (1, 80))
NOTIFY_TITLE = "fable-meter"
# resets_at はフェッチごとに秒未満が揺れるので、この秒数以下の前進は無視する。
RESET_JITTER_SECONDS = 60


class FetchError(Exception):
    """Carries a machine-readable error code. Never carries token material."""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------- log

def _ensure_private_dir(path=CACHE_DIR):
    """Create `path` 0700 and re-assert the mode if it already existed."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _open_private(path, mode):
    """Open `path` for writing with 0600 from the moment of creation."""
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if mode == "a" else os.O_TRUNC)
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        pass
    return os.fdopen(fd, mode, encoding="utf-8")


def log(message, verbose=False):
    """Append a line to fetch.log. Callers must never pass token material."""
    line = "%s %s" % (datetime.now(timezone.utc).astimezone().isoformat(), message)
    try:
        _ensure_private_dir()
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
            os.replace(LOG_PATH, LOG_PATH + ".1")
        with _open_private(LOG_PATH, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    if verbose:
        sys.stderr.write(line + "\n")


# ------------------------------------------------------------------- wake check

def waketime_seconds(sysctl_output):
    """Parse `sysctl -n kern.waketime` output -> epoch seconds, or None."""
    if not sysctl_output:
        return None
    text = sysctl_output.strip()
    # Format: { sec = 1756450000, usec = 123456 } Fri Aug 29 21:00:00 2026
    marker = "sec = "
    idx = text.find(marker)
    if idx == -1:
        return None
    rest = text[idx + len(marker):]
    digits = ""
    for ch in rest:
        if ch.isdigit():
            digits += ch
        else:
            break
    if not digits:
        return None
    return int(digits)


def recently_woke(now=None):
    """True if the machine woke from sleep within WAKE_SKIP_SECONDS."""
    now = time.time() if now is None else now
    try:
        out = subprocess.run(
            [SYSCTL_BIN, "-n", "kern.waketime"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    wake = waketime_seconds(out)
    if wake is None:
        return False
    return 0 <= (now - wake) < WAKE_SKIP_SECONDS


# --------------------------------------------------------------------- keychain

def read_token():
    """Return (access_token, expires_at_ms, plan). Never log or print the token."""
    try:
        proc = subprocess.run(
            [SECURITY_BIN, "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        raise FetchError("keychain_error")
    if proc.returncode != 0:
        raise FetchError("keychain_error")
    raw = proc.stdout.strip()
    if not raw:
        raise FetchError("keychain_error")
    try:
        blob = json.loads(raw)
        oauth = blob["claudeAiOauth"]
        token = oauth["accessToken"]
        expires_at = oauth.get("expiresAt")
        plan = oauth.get("subscriptionType")
    except (ValueError, KeyError, TypeError):
        raise FetchError("keychain_parse_error")
    if not isinstance(token, str) or not token:
        raise FetchError("keychain_parse_error")
    if not isinstance(plan, str):
        plan = None
    return token, expires_at, plan


def token_is_expired(expires_at_ms, now_ms=None):
    if expires_at_ms is None:
        return False
    try:
        expires_at_ms = float(expires_at_ms)
    except (TypeError, ValueError):
        return False
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    return expires_at_ms <= now_ms


# ------------------------------------------------------------------------- http

def http_get_usage(token, verbose=False):
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": "Bearer " + token,
            "anthropic-beta": "oauth-2025-04-20",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            status = resp.getcode()
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        raise FetchError(*_classify_http(status, body))
    except urllib.error.URLError:
        raise FetchError("network_error")
    except (OSError, ValueError):
        raise FetchError("network_error")
    if status != 200:
        raise FetchError(*_classify_http(status, body))
    log("http 200 body_bytes=%d" % len(body), verbose)
    try:
        return json.loads(body)
    except ValueError:
        log("schema_error: body[:300]=%s" % body[:300], verbose)
        raise FetchError("schema_error")


def _classify_http(status, body):
    snippet = (body or "")[:300]
    if status in (401, 403):
        etype = None
        try:
            etype = json.loads(body).get("error", {}).get("type")
        except Exception:
            etype = None
        return ("auth_error", etype or snippet)
    if status == 429:
        return ("rate_limited", snippet)
    return ("http_%d" % status, snippet)


# ------------------------------------------------------------------------ parse

def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FetchError("schema_error")
    return value


def _entry_from_limit(limit):
    return {
        "percent": _num(limit.get("percent")),
        "resets_at": limit.get("resets_at"),
        "severity": limit.get("severity"),
    }


def _entry_from_toplevel(block):
    if not isinstance(block, dict):
        return None
    if "utilization" not in block:
        return None
    return {
        "percent": _num(block.get("utilization")),
        "resets_at": block.get("resets_at"),
        "severity": block.get("severity"),
    }


def parse_usage(payload, plan=None):
    """Turn the API payload into the state.json `data` object.

    Raises FetchError('schema_error') or FetchError('fable_not_found').
    """
    if not isinstance(payload, dict):
        raise FetchError("schema_error")
    limits = payload.get("limits")
    if not isinstance(limits, list):
        raise FetchError("schema_error")

    five_hour = None
    seven_day = None
    scoped = []
    for limit in limits:
        if not isinstance(limit, dict):
            raise FetchError("schema_error")
        kind = limit.get("kind")
        if kind == "session" and five_hour is None:
            five_hour = _entry_from_limit(limit)
        elif kind == "weekly_all" and seven_day is None:
            seven_day = _entry_from_limit(limit)
        elif kind == "weekly_scoped":
            scope = limit.get("scope") or {}
            model = scope.get("model") or {}
            name = model.get("display_name") or scope.get("surface") or "unknown"
            entry = _entry_from_limit(limit)
            entry["name"] = name
            scoped.append({
                "name": name,
                "percent": entry["percent"],
                "resets_at": entry["resets_at"],
                "severity": entry["severity"],
            })

    if five_hour is None:
        five_hour = _entry_from_toplevel(payload.get("five_hour"))
    if seven_day is None:
        seven_day = _entry_from_toplevel(payload.get("seven_day"))

    fable = None
    for item in scoped:
        if isinstance(item.get("name"), str) and item["name"].lower() == "fable":
            fable = {
                "percent": item["percent"],
                "resets_at": item["resets_at"],
                "severity": item["severity"],
            }
            break
    if fable is None:
        raise FetchError("fable_not_found")

    if not isinstance(plan, str):
        plan = payload.get("plan")
    if not isinstance(plan, str):
        plan = None

    return {
        "fable": fable,
        "seven_day": seven_day,
        "five_hour": five_hour,
        "scoped": scoped,
        "plan": plan,
    }


# ---------------------------------------------------------------------- history

def _iso_now(now=None):
    return (now or datetime.now(timezone.utc).astimezone()).isoformat()


def parse_iso(text):
    """ISO8601 -> aware datetime, or None. Naive strings are treated as UTC."""
    if not isinstance(text, str) or not text:
        return None
    value = text.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def history_entry(data, now=None):
    """Build one history record from a parsed `data` object, or None."""
    if not isinstance(data, dict):
        return None
    fable = data.get("fable") or {}
    percent = fable.get("percent")
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        return None
    seven = data.get("seven_day") or {}
    seven_percent = seven.get("percent")
    if isinstance(seven_percent, bool) or not isinstance(seven_percent, (int, float)):
        seven_percent = None
    else:
        seven_percent = int(round(seven_percent))
    return {
        "t": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
        "fable": int(round(percent)),
        "seven_day": seven_percent,
        "fable_resets_at": fable.get("resets_at"),
    }


def prune_history_lines(lines, now=None, max_days=HISTORY_MAX_DAYS):
    """Drop lines whose `t` is older than max_days (or unparseable)."""
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - max_days * 86400
    kept = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        stamp = parse_iso(entry.get("t"))
        if stamp is None or stamp.timestamp() < cutoff:
            continue
        kept.append(line)
    return kept


def append_history(data, now=None, path=HISTORY_PATH):
    """Append one 0600 JSONL line, pruning entries older than HISTORY_MAX_DAYS.

    A single launchd writer means a plain append is enough; a concurrent manual
    refresh can at worst duplicate a line, which the projection tolerates.
    """
    entry = history_entry(data, now=now)
    if entry is None:
        return None
    directory = os.path.dirname(path)
    if directory:
        _ensure_private_dir(directory)
    line = json.dumps(entry, ensure_ascii=False)
    with _open_private(path, "a") as fh:
        fh.write(line + "\n")
    # Prune only when something actually fell out of the window, so the common
    # case stays a pure append.
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return entry
    kept = prune_history_lines(lines, now=now)
    if len(kept) != len(lines):
        tmp = path + ".tmp"
        with _open_private(tmp, "w") as fh:
            fh.write("\n".join(kept) + ("\n" if kept else ""))
        os.replace(tmp, path)
    return entry


# ----------------------------------------------------------------- notification

def band_for_percent(percent):
    """0 (<80), 1 (>=80), 2 (>=95). Non-numeric -> 0."""
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        return 0
    for band, threshold in NOTIFY_THRESHOLDS:
        if percent >= threshold:
            return band
    return 0


def stored_band(state):
    band = (state or {}).get("last_notified_band") if isinstance(state, dict) else None
    if isinstance(band, bool) or not isinstance(band, int):
        return 0
    return band if 0 <= band <= 2 else 0


def window_reset(previous_state, fable):
    """True if the weekly window rolled over since the previous state."""
    previous = (previous_state or {}).get("data") if isinstance(previous_state, dict) else None
    previous = (previous or {}).get("fable") if isinstance(previous, dict) else None
    if not isinstance(previous, dict) or not isinstance(fable, dict):
        return False
    old_pct, new_pct = previous.get("percent"), fable.get("percent")
    if not isinstance(old_pct, bool) and isinstance(old_pct, (int, float)) \
            and not isinstance(new_pct, bool) and isinstance(new_pct, (int, float)) \
            and new_pct < old_pct:
        return True
    old_reset = parse_iso(previous.get("resets_at"))
    new_reset = parse_iso(fable.get("resets_at"))
    # The API jitters resets_at by fractions of a second between fetches, so
    # only a move of at least RESET_JITTER_SECONDS counts as a new window.
    if old_reset is not None and new_reset is not None \
            and (new_reset - old_reset).total_seconds() > RESET_JITTER_SECONDS:
        return True
    return False


def evaluate_band(previous_state, fable):
    """Return (band_to_store, percent_to_notify_or_None).

    Fires at most once per band per window; a detected reset clears the band.
    """
    current = band_for_percent((fable or {}).get("percent"))
    last = 0 if window_reset(previous_state, fable) else stored_band(previous_state)
    if current > last:
        return current, int(round(fable["percent"]))
    return max(last, current), None


def notify(band, percent, verbose=False):
    """Fire a macOS notification. Only the integer percent is interpolated."""
    threshold = dict((b, t) for b, t in NOTIFY_THRESHOLDS).get(band)
    if threshold is None:
        return False
    script = 'display notification "Fable が%d%%を超えました(現在 %d%%)" with title "%s"' % (
        threshold, int(percent), NOTIFY_TITLE)
    try:
        subprocess.run([OSASCRIPT_BIN, "-e", script],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        log("notify_failed band=%d" % band, verbose)
        return False
    log("notified band=%d" % band, verbose)
    return True


# ------------------------------------------------------------------------ state

def load_state(path=STATE_PATH):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    return state


def build_success_state(data, now=None, last_notified_band=0):
    now = now or datetime.now(timezone.utc).astimezone()
    return {
        "schema": SCHEMA,
        "ok": True,
        "fetched_at": now.isoformat(),
        "error": None,
        "error_at": None,
        "data": data,
        "last_notified_band": stored_band({"last_notified_band": last_notified_band}),
    }


def build_error_state(code, previous=None, now=None):
    """Keep the previous data/fetched_at so the UI can judge staleness."""
    now = now or datetime.now(timezone.utc).astimezone()
    previous = previous if isinstance(previous, dict) else {}
    return {
        "schema": SCHEMA,
        "ok": False,
        "fetched_at": previous.get("fetched_at"),
        "error": code,
        "error_at": now.isoformat(),
        "data": previous.get("data"),
        "last_notified_band": stored_band(previous),
    }


def write_state(state, path=STATE_PATH):
    directory = os.path.dirname(path)
    if directory:
        _ensure_private_dir(directory)
    tmp = path + ".tmp"
    with _open_private(tmp, "w") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


# ------------------------------------------------------------------------- main

def run(dry_run=False, verbose=False, force=False):
    if not force and recently_woke():
        log("skip: recent wake", verbose)
        return 0

    previous = load_state()
    try:
        token, expires_at, plan = read_token()
        try:
            if token_is_expired(expires_at):
                raise FetchError("token_expired")
            payload = http_get_usage(token, verbose=verbose)
        finally:
            del token
        data = parse_usage(payload, plan=plan)
    except FetchError as exc:
        detail = (" detail=%s" % exc.detail) if exc.detail else ""
        log("error=%s%s" % (exc.code, detail), verbose)
        state = build_error_state(exc.code, previous)
        if dry_run:
            json.dump(state, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            try:
                write_state(state)
            except OSError:
                log("error=state_write_failed", verbose)
        return 1

    band, notify_percent = evaluate_band(previous, data["fable"])
    state = build_success_state(data, last_notified_band=band)
    log("ok fable=%s weekly=%s session=%s" % (
        data["fable"]["percent"],
        data["seven_day"]["percent"] if data["seven_day"] else None,
        data["five_hour"]["percent"] if data["five_hour"] else None,
    ), verbose)
    if dry_run:
        # --dry-run has no side effects: no state, no history, no notification.
        json.dump(state, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    write_state(state)
    try:
        append_history(data)
    except OSError:
        log("error=history_write_failed", verbose)
    if notify_percent is not None:
        notify(band, notify_percent, verbose)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="fetch.py", description="Fetch Claude Code usage into ~/.cache/fable-meter/state.json"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="print the state JSON to stdout instead of writing it")
    parser.add_argument("--verbose", action="store_true", help="also log to stderr")
    parser.add_argument("--force", action="store_true", help="ignore the post-wake skip")
    args = parser.parse_args(argv)
    return run(dry_run=args.dry_run, verbose=args.verbose, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
