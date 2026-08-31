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
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "fable-meter")
STATE_PATH = os.path.join(CACHE_DIR, "state.json")
LOG_PATH = os.path.join(CACHE_DIR, "fetch.log")
LOG_MAX_BYTES = 200 * 1024
HTTP_TIMEOUT = 15
WAKE_SKIP_SECONDS = 60


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


def build_success_state(data, now=None):
    now = now or datetime.now(timezone.utc).astimezone()
    return {
        "schema": SCHEMA,
        "ok": True,
        "fetched_at": now.isoformat(),
        "error": None,
        "error_at": None,
        "data": data,
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

    state = build_success_state(data)
    log("ok fable=%s weekly=%s session=%s" % (
        data["fable"]["percent"],
        data["seven_day"]["percent"] if data["seven_day"] else None,
        data["five_hour"]["percent"] if data["five_hour"] else None,
    ), verbose)
    if dry_run:
        json.dump(state, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        write_state(state)
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
