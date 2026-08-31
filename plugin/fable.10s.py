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
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>
# <swiftbar.runInBash>false</swiftbar.runInBash>
"""SwiftBar display layer. Reads state.json only -- no network, no Keychain."""

import json
import math
import os
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "fable-meter")
STATE_PATH = os.path.join(CACHE_DIR, "state.json")
LOG_PATH = os.path.join(CACHE_DIR, "fetch.log")
HISTORY_PATH = os.path.join(CACHE_DIR, "history.jsonl")
USAGE_PAGE_URL = "https://claude.ai/settings/usage"
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# install.sh がプラグインをコピーする際、この行を絶対パスに書き換える。
FETCH_PATH = ""


def default_fetch_path():
    if FETCH_PATH and os.path.exists(FETCH_PATH):
        return FETCH_PATH
    return os.path.join(REPO_DIR, "fetch.py")

STALE_SECONDS = 10 * 60
DEAD_SECONDS = 30 * 60

# 色はメニューバーのタイトル行だけに付ける。
COLOR_WARN = "#e0a800"
COLOR_CRIT = "#d0021b"
COLOR_GRAY = "#8e8e93"

# ドロップダウンの情報行には color= を**付けない**。
# SwiftBar の MenuBarItem.configureAction() は
#   if params.hasAction || params.color != nil { item.action = ... }
# となっており(SwiftBar/MenuBar/MenuBarItem.swift)、color= を付けただけの行も
# クリック可能な項目になってしまう(ホバーで選択ハイライトが出る)。
# SwiftBar に disabled= 相当のパラメータは無いので、状態表示の行は
#   「色を付けない = アクションを持たない = 無効項目」
# として macOS 標準の淡色描画に任せる。ハイライトしないことを優先する。
ERROR_PREFIX = "\u26a0\ufe0f "

LABEL_WIDTH = 16

# ペース予測: 現在の窓に 2 点以上あり、その間隔が 3 時間以上あるときだけ出す。
PROJECTION_MIN_POINTS = 2
PROJECTION_MIN_SPAN_SECONDS = 3 * 3600
PROJECTION_MAX_PERCENT = 200
HISTORY_MAX_LINES = 4000


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
        return "%d日%d時間" % (days, hours)
    if hours:
        return "%d時間%d分" % (hours, minutes)
    return "%d分" % minutes


def display_width(text):
    """Rough monospace cell count: CJK/full-width glyphs occupy two cells."""
    width = 0
    for ch in text:
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def pad_label(text, width=LABEL_WIDTH):
    return text + " " * max(0, width - display_width(text))


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
        return "リセット %s" % stamp
    return "リセット %s (あと%s)" % (stamp, fmt_duration(delta))


def read_history(path=HISTORY_PATH, max_lines=HISTORY_MAX_LINES):
    """Return history records oldest-first. Unreadable file -> []."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except (OSError, ValueError):
        return []
    entries = []
    for line in lines[-max_lines:]:
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
        percent = entry.get("fable")
        if stamp is None or isinstance(percent, bool) \
                or not isinstance(percent, (int, float)):
            continue
        entries.append({
            "t": stamp,
            "fable": float(percent),
            "fable_resets_at": entry.get("fable_resets_at"),
        })
    entries.sort(key=lambda e: e["t"])
    return entries


def reset_key(text):
    """Normalize a resets_at string for comparison.

    The API jitters the sub-second part of resets_at on every fetch, so string
    equality would make every sample look like its own window. Compare at
    minute granularity instead.
    """
    dt = parse_iso(text)
    if dt is None:
        return None
    return int(dt.timestamp() // 60)


def window_points(entries, resets_at):
    """Keep only the points belonging to the current window.

    Walking backwards from the newest point, stop at the first entry whose
    resets_at differs from the current one, or whose percent is *higher* than
    the point after it -- going forward that is a drop, i.e. a weekly reset.
    """
    points = []
    newer_percent = None
    target = reset_key(resets_at)
    for entry in reversed(entries or []):
        entry_key = reset_key(entry.get("fable_resets_at"))
        if target is not None and entry_key is not None and entry_key != target:
            break
        if newer_percent is not None and entry["fable"] > newer_percent:
            break
        points.append(entry)
        newer_percent = entry["fable"]
    points.reverse()
    return points


def project(points, resets_at, now=None):
    """Linear pace projection for the current window.

    Returns None (not enough data), ("reset", percent) for the value expected at
    the reset, or ("cross", datetime) for when 100% is expected to be reached.
    """
    if len(points) < PROJECTION_MIN_POINTS or resets_at is None:
        return None
    first, last = points[0], points[-1]
    span = (last["t"] - first["t"]).total_seconds()
    if span < PROJECTION_MIN_SPAN_SECONDS:
        return None
    remaining = (resets_at - last["t"]).total_seconds()
    if remaining <= 0:
        return None
    slope = (last["fable"] - first["fable"]) / span
    projected = last["fable"] + slope * remaining
    projected = max(last["fable"], min(PROJECTION_MAX_PERCENT, projected))
    # Already past 100%: there is no future crossing to report, just the value.
    if projected > 100 and slope > 0 and last["fable"] < 100:
        cross = last["t"] + timedelta(seconds=(100.0 - last["fable"]) / slope)
        return ("cross", cross)
    return ("reset", projected)


def collecting_hours(points, now):
    """Hours left until the current window spans enough time to project."""
    if not points:
        return int(math.ceil(PROJECTION_MIN_SPAN_SECONDS / 3600.0))
    remaining = PROJECTION_MIN_SPAN_SECONDS - (now - points[0]["t"]).total_seconds()
    return max(1, int(math.ceil(remaining / 3600.0)))


def projection_row(state, now, history=None):
    """The 予測 line, or None when the state itself does not support one."""
    if not isinstance(state, dict) or state.get("ok") is not True:
        return None
    age = age_seconds(state, now)
    if age is None or age >= STALE_SECONDS:
        return None
    data = state.get("data")
    if not isinstance(data, dict):
        return None
    fable = data.get("fable")
    if not isinstance(fable, dict):
        return None
    resets_at_text = fable.get("resets_at")
    resets_at = parse_iso(resets_at_text)
    if resets_at is None or (resets_at - now).total_seconds() <= 0:
        return None
    if history is None:
        history = read_history()
    points = window_points(history, resets_at_text)
    result = project(points, resets_at, now)
    if result is None:
        # 状態は新しいのに履歴が足りないだけ: 収集中であることを出す。
        return "予測: データ収集中(あと約%d時間)" % collecting_hours(points, now)
    kind, value = result
    if kind == "cross":
        local = value.astimezone()
        return "予測: 100%%到達 %sごろ" % local.strftime("%-m/%-d %-H時")
    return "予測: リセット時点 ~%d%%" % int(round(value))


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

    title = "F%s%%%s W%s%%%s S%s%%%s" % (values[0], suffix, values[1], suffix,
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


def render(state, now=None, python_path=None, fetch_path=None, log_path=LOG_PATH,
           history=None):
    now = now or datetime.now(timezone.utc).astimezone()
    python_path = python_path or sys.executable or "/usr/bin/python3"
    fetch_path = fetch_path or default_fetch_path()

    lines = [title_line(state, now), "---"]

    data = (state or {}).get("data") if isinstance(state, dict) else None
    if isinstance(data, dict):
        rows = (("Fable", data.get("fable")),
                ("週間(全モデル)", data.get("seven_day")),
                ("セッション(5h)", data.get("five_hour")))
        for label, entry in rows:
            if not isinstance(entry, dict):
                lines.append("%s -- | font=Menlo size=12" % pad_label(label))
                continue
            reset = fmt_reset(entry.get("resets_at"), now)
            lines.append(("%s %3s%%   %s" % (
                pad_label(label), fmt_percent(entry.get("percent")), reset)).rstrip()
                + " | font=Menlo size=12")
        projection = projection_row(state, now, history)
        if projection:
            lines.append(projection)
        lines.append("---")
        age = age_seconds(state, now)
        fetched = parse_iso(state.get("fetched_at"))
        if fetched is not None:
            stamp = fetched.astimezone().strftime("%H:%M:%S")
            lines.append("取得: %s (%s前)" % (stamp, fmt_duration(age)))
        else:
            lines.append("取得: なし")
    else:
        lines.append("まだデータがありません")
        lines.append("---")

    if not isinstance(state, dict):
        lines.append("%sエラー: state.json が見つからないか読めません" % ERROR_PREFIX)
    elif state.get("error"):
        at = parse_iso(state.get("error_at"))
        stamp = at.astimezone().strftime("%H:%M:%S") if at else "?"
        lines.append("%sエラー: %s (%s)"
                     % (ERROR_PREFIX, state.get("error"), stamp))

    lines.append("---")
    lines.append("リフレッシュ | bash=%s param1=%s param2=--force terminal=false "
                 "refresh=true sfimage=arrow.clockwise" % (python_path, fetch_path))
    lines.append("使用量ページを開く | href=%s sfimage=safari" % USAGE_PAGE_URL)
    lines.append("ログを開く | bash=/usr/bin/open param1=%s terminal=false "
                 "sfimage=doc.text" % log_path)
    return "\n".join(lines)


def main():
    sys.stdout.write(render(read_state()) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
