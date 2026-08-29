#!/usr/bin/env python3
# <bitbar.title>fable-meter</bitbar.title>
# <bitbar.version>1.0</bitbar.version>
# <bitbar.author>fable-meter</bitbar.author>
# <bitbar.desc>Claude Code Fable / weekly / session usage from ~/.cache/fable-meter/state.json</bitbar.desc>
# <bitbar.dependencies>python3</bitbar.dependencies>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>false</swiftbar.hideSwiftBar>
# <swiftbar.runInBash>false</swiftbar.runInBash>
"""SwiftBar display layer. Reads state.json only -- no network, no Keychain."""

import json
import os
import sys
from datetime import datetime, timezone

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "fable-meter")
STATE_PATH = os.path.join(CACHE_DIR, "state.json")
LOG_PATH = os.path.join(CACHE_DIR, "fetch.log")
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# install.sh がプラグインをコピーする際、この行を絶対パスに書き換える。
FETCH_PATH = ""


def default_fetch_path():
    if FETCH_PATH and os.path.exists(FETCH_PATH):
        return FETCH_PATH
    return os.path.join(REPO_DIR, "fetch.py")

STALE_SECONDS = 10 * 60
DEAD_SECONDS = 30 * 60

COLOR_WARN = "#e0a800"
COLOR_CRIT = "#d0021b"
COLOR_GRAY = "#8e8e93"
COLOR_ERROR = "#d0021b"


def read_state(path=STATE_PATH):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    return state


def parse_iso(text):
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


def age_seconds(state, now):
    dt = parse_iso((state or {}).get("fetched_at"))
    if dt is None:
        return None
    return (now - dt).total_seconds()


def fmt_percent(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "--"
    return str(int(round(value)))


def fmt_duration(seconds):
    seconds = int(max(0, seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return "%dd %dh" % (days, hours)
    if hours:
        return "%dh %dm" % (hours, minutes)
    return "%dm" % minutes


def fmt_reset(text, now):
    dt = parse_iso(text)
    if dt is None:
        return ""
    local = dt.astimezone()
    delta = (dt - now).total_seconds()
    if delta >= 86400:
        stamp = local.strftime("%-m/%-d %H:%M")
    else:
        stamp = local.strftime("%H:%M")
    if delta <= 0:
        return "resets %s" % stamp
    return "resets %s (in %s)" % (stamp, fmt_duration(delta))


def build_title(state, now):
    """Return (title_line, is_degraded)."""
    data = (state or {}).get("data") if isinstance(state, dict) else None
    age = age_seconds(state, now) if isinstance(state, dict) else None

    if not isinstance(state, dict) or not isinstance(data, dict) \
            or age is None or age >= DEAD_SECONDS:
        return "F-- W-- S--", True

    fable = data.get("fable") or {}
    weekly = data.get("seven_day") or {}
    session = data.get("five_hour") or {}
    values = (fmt_percent(fable.get("percent")),
              fmt_percent(weekly.get("percent")),
              fmt_percent(session.get("percent")))

    if age >= STALE_SECONDS:
        suffix = "?"
        degraded = True
    elif state.get("ok") is False:
        suffix = "!"
        degraded = False
    else:
        suffix = ""
        degraded = False

    title = "F%s%s W%s%s S%s%s" % (values[0], suffix, values[1], suffix,
                                   values[2], suffix)
    return title, degraded


def title_color(state, now, degraded):
    if degraded:
        return COLOR_GRAY
    data = (state or {}).get("data") or {}
    fable = data.get("fable") or {}
    percent = fable.get("percent")
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        return COLOR_GRAY
    if percent > 95:
        return COLOR_CRIT
    if percent > 80:
        return COLOR_WARN
    return None


def title_line(state, now=None):
    now = now or datetime.now(timezone.utc).astimezone()
    title, degraded = build_title(state, now)
    color = title_color(state, now, degraded)
    if color:
        return "%s | color=%s" % (title, color)
    return title


def render(state, now=None, python_path=None, fetch_path=None, log_path=LOG_PATH):
    now = now or datetime.now(timezone.utc).astimezone()
    python_path = python_path or sys.executable or "/usr/bin/python3"
    fetch_path = fetch_path or default_fetch_path()

    lines = [title_line(state, now), "---"]

    data = (state or {}).get("data") if isinstance(state, dict) else None
    if isinstance(data, dict):
        rows = (("Fable", data.get("fable")),
                ("Weekly", data.get("seven_day")),
                ("Session", data.get("five_hour")))
        for label, entry in rows:
            if not isinstance(entry, dict):
                lines.append("%-8s --" % label)
                continue
            reset = fmt_reset(entry.get("resets_at"), now)
            lines.append(("%-8s %3s%%   %s" % (
                label, fmt_percent(entry.get("percent")), reset)).rstrip()
                + " | font=Menlo size=12")
        lines.append("---")
        plan = data.get("plan") or "-"
        age = age_seconds(state, now)
        fetched = parse_iso(state.get("fetched_at"))
        if fetched is not None:
            stamp = fetched.astimezone().strftime("%H:%M:%S")
            lines.append("Plan: %s · Fetched: %s (%s ago)"
                         % (plan, stamp, fmt_duration(age)))
        else:
            lines.append("Plan: %s · Fetched: never" % plan)
    else:
        lines.append("No data yet | color=%s" % COLOR_GRAY)
        lines.append("---")

    if not isinstance(state, dict):
        lines.append("Error: state.json missing or unreadable | color=%s" % COLOR_ERROR)
    elif state.get("error"):
        at = parse_iso(state.get("error_at"))
        stamp = at.astimezone().strftime("%H:%M:%S") if at else "?"
        lines.append("Error: %s (%s) | color=%s"
                     % (state.get("error"), stamp, COLOR_ERROR))

    lines.append("---")
    lines.append("Refresh now | bash=%s param1=%s param2=--force terminal=false refresh=true"
                 % (python_path, fetch_path))
    lines.append("Open log | bash=/usr/bin/open param1=%s terminal=false" % log_path)
    return "\n".join(lines)


def main():
    sys.stdout.write(render(read_state()) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
