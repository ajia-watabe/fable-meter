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
# 設定はキャッシュではないので ~/.config に置く(uninstall.sh --purge で消えない)。
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "fable-meter")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
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

# ドロップダウンの情報行は「色は付ける・クリックはさせない」。
#
# SwiftBar v2.1.1 の buildMenuItem()/patchMenuItem() は
#   let needsAction = params.hasAction || params.color != nil
# (SwiftBar/MenuBar/MenuBarItem.swift:1518, :973)なので、`color=` を付けた行は
# それだけでクリック可能な項目になる。
# 一方 `ansi=true` は hasAction にも color にも影響しないので、
# ANSI エスケープで色を付ければ「無効項目のまま色が付く」。
#
# 注意: macOS 26(Tahoe)では**無効項目でもホバーで紫のハイライトが描かれる**。
# これは AppKit の描画で、色を一切付けていない行(取得:)でも同じように出る
# (macOS 26.3 / SwiftBar 2.1.1 で全行を実測。Accessibility 上は enabled=false)。
# プラグイン側でもSwiftBar のバージョンでも抑止できないため、
# 「クリックは効かない・見た目のハイライトだけは出る」で確定。
# atributedTitle() は ansi のとき params.color を上書きせず
# (MenuBarItem.swift:1559-1566)、font/size は ansi でも最後に適用されるので
# font=Menlo size=12 と併用できる。
#
# 無効(action=nil)な NSMenuItem でも、attributedTitle に**有彩色**を入れておけば
# AppKit はその色をそのまま描く。ただし r==g==b の**無彩色**(グレー・白・黒)は
# AppKit が無効項目用の淡色に差し替えてしまう(実測: (188,188,188) は淡色化、
# (188,188,190) はそのまま描画)。SwiftBar の ANSI 256 色のうち 232-255 の
# グレースケール段は完全な無彩色なので、この用途には使えない。
ESC = "\x1b"
# 31 -> NSColor.systemRed(SwiftBar/Utility/String+ANSIColor.swift:9)。
# 動的カラーなのでライト/ダーク双方で読める。
ANSI_ERROR = ESC + "[31m"
# 256 色番号 189 は SwiftBar の変換式で (247,248,255) になる。
# ほぼ白だが無彩色ではないので無効項目でもそのまま描かれる = ダークでの本文色。
# ライトには「黒に近い有彩色」が ANSI パレットに無いので、ライトでは色を付けない
# (macOS 標準の無効項目描画に任せる)。
ANSI_PRIMARY_DARK = ESC + "[38;5;189m"


def is_dark_appearance(env=None):
    """SwiftBar は OS_APPEARANCE=Dark/Light をプラグインに渡す。

    (SwiftBar/Plugin/Plugin.swift:296, SwiftBar/Utility/Environment.swift:19)
    テーマ切替でプラグインは再実行されず既存の出力を再描画するだけなので、
    切り替え直後の最大10秒は前のテーマの色のままになる。
    """
    env = os.environ if env is None else env
    return (env.get("OS_APPEARANCE") or "").strip().lower() == "dark"


def ansi_row(text, code, params=""):
    """色付き・アクション無しの行を作る。code が None なら素のまま。"""
    tail = (" " + params).rstrip()
    if not code:
        return "%s |%s" % (text, tail) if tail else text
    return "%s%s | ansi=true%s" % (code, text, tail)


# 等幅で桁を揃えるためのラベル幅(表示セル数)。日本語は全角ラベルが 14 セル、
# 英語は "Weekly (all models)" が 19 セルなので言語ごとに変える。
LABEL_WIDTH = 16
LABEL_WIDTH_EN = 21
LABEL_WIDTHS = {"ja": LABEL_WIDTH, "en": LABEL_WIDTH_EN}

# 既定の言語。実際の言語は resolve_lang()(config.json + state.locale_lang)で決まり、
# main() だけが解決する。ここは「引数を省いたときの言語」= このツールの原語。
DEFAULT_LANG = "ja"
LANGS = ("ja", "en")

# ユーザーに見える文字列は全てここに置く。書式指定子の数と並びは両言語で同じ。
STRINGS = {
    "ja": {
        "label_fable": "Fable",
        "label_weekly": "週間(全モデル)",
        "label_session": "セッション(5h)",
        "proj_fable": "予測 Fable",
        "proj_weekly": "予測 週間",
        "proj_prefix": "予測",
        "reset": "リセット %s",
        "reset_left": "リセット %s (あと%s)",
        "date_reset": "%-m/%-d %H:%M",
        "dur_days": "%d日%d時間",
        "dur_hours": "%d時間%d分",
        "dur_minutes": "%d分",
        "at_reset": "リセット時点 ~%d%%",
        "cross": "100%%到達 %sごろ",
        "date_cross": "%-m/%-d %-H時",
        "collecting": "データ収集中(%s)",
        "collecting_hours": "あと約%d時間",
        "collecting_minutes": "あと約%d分",
        "fetched": "取得: %s (%s前)",
        "fetched_none": "取得: なし",
        "error": "エラー: %s (%s)",
        "error_no_state": "エラー: state.json が見つからないか読めません",
        "no_data": "まだデータがありません",
        "refresh": "リフレッシュ",
        "open_usage": "使用量ページを開く",
        "open_log": "ログを開く",
    },
    "en": {
        "label_fable": "Fable",
        "label_weekly": "Weekly (all models)",
        "label_session": "Session (5h)",
        "proj_fable": "Forecast Fable",
        "proj_weekly": "Forecast Weekly",
        "proj_prefix": "Forecast",
        "reset": "resets %s",
        "reset_left": "resets %s (%s left)",
        "date_reset": "%b %-d %H:%M",
        "dur_days": "%dd %dh",
        "dur_hours": "%dh %dm",
        "dur_minutes": "%dm",
        "at_reset": "~%d%% at reset",
        "cross": "hits 100%% ~%s",
        "date_cross": "%b %-d, %-I%p",
        "collecting": "collecting data (%s)",
        "collecting_hours": "~%dh left",
        "collecting_minutes": "~%dm left",
        "fetched": "fetched: %s (%s ago)",
        "fetched_none": "fetched: none",
        "error": "Error: %s (%s)",
        "error_no_state": "Error: state.json is missing or unreadable",
        "no_data": "No data yet",
        "refresh": "Refresh",
        "open_usage": "Open usage page",
        "open_log": "Open log",
    },
}


def strings(lang=DEFAULT_LANG):
    return STRINGS.get(lang) or STRINGS[DEFAULT_LANG]


def read_config(path=CONFIG_PATH):
    """~/.config/fable-meter/config.json。無い/壊れている → {}(既定に落ちる)。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, ValueError):
        return {}
    return config if isinstance(config, dict) else {}


def resolve_lang(config=None, state=None):
    """明示指定 > state の locale_lang(fetch.py が解決したシステムロケール)> en。

    config.json は表示層が毎回読む(ローカルの小さな JSON なので十分速い)。
    "auto" のときのシステムロケール解決は取得層に任せる: SwiftBar の環境には
    LANG が無いことが多く、プラグインからは判定できないため。
    """
    value = (config or {}).get("lang")
    if isinstance(value, str) and value.strip().lower() in LANGS:
        return value.strip().lower()
    stored = (state or {}).get("locale_lang") if isinstance(state, dict) else None
    if isinstance(stored, str) and stored.strip().lower() in LANGS:
        return stored.strip().lower()
    return "en"


# 等幅で桁を揃えるための共通パラメータ。
MONO = "font=Menlo size=12"

# 予測パイプラインが既定で見る history.jsonl の値のキー。
DEFAULT_METRIC = "fable"

# ペース予測: 現在の窓に 2 点以上あり、その間隔が 3 時間以上あるときだけ出す。
PROJECTION_MIN_POINTS = 2
PROJECTION_MIN_SPAN_SECONDS = 3 * 3600
PROJECTION_MAX_PERCENT = 200
HISTORY_MAX_LINES = 4000

# 成分A(直近重み付き傾き): 重み w_i = 0.5 ** (経過時間h / 半減期)。
# 12 時間で重み半分 = 「今日の後半」が「昨日」の倍効く。
SLOPE_HALF_LIFE_HOURS = 12.0

# 成分B(1日の活動カーブ): 24 個の時刻バケット。
CURVE_BUCKETS = 24
# カーブを使うには履歴全体で 24 時間以上の観測が要る。
CURVE_MIN_SPAN_SECONDS = 24 * 3600
# 時刻境界を歩くループの安全上限(履歴8日・窓7日のどちらも 200 時間未満)。
CURVE_MAX_STEPS = 24 * 40


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


def fmt_duration(seconds, lang=DEFAULT_LANG):
    text = strings(lang)
    seconds = int(max(0, seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return text["dur_days"] % (days, hours)
    if hours:
        return text["dur_hours"] % (hours, minutes)
    return text["dur_minutes"] % minutes


def display_width(text):
    """Rough monospace cell count: CJK/full-width glyphs occupy two cells."""
    width = 0
    for ch in text:
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def truncate_label(text, width):
    """Cut `text` down to `width` display cells, marking the cut with "…".

    スコープ枠のラベルは API が返すモデル名なので、ラベル幅より長いことがある。
    そのまま出すと右側の % とリセットの桁が全部ずれるので、幅で切って詰める。
    """
    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text
    kept = ""
    used = 0
    budget = width - display_width("…")
    for ch in text:
        cell = display_width(ch)
        if used + cell > budget:
            break
        kept += ch
        used += cell
    return kept + "…"


def pad_label(text, width=None, lang=DEFAULT_LANG):
    if width is None:
        width = LABEL_WIDTHS.get(lang, LABEL_WIDTH)
    text = truncate_label(text, width)
    return text + " " * max(0, width - display_width(text))


def fmt_reset(text, now, lang=DEFAULT_LANG):
    labels = strings(lang)
    dt = parse_iso(text)
    if dt is None:
        return ""
    local = dt.astimezone()
    delta = (dt - now).total_seconds()
    if delta >= 86400:
        stamp = local.strftime(labels["date_reset"])
    else:
        stamp = local.strftime("%H:%M")
    if delta <= 0:
        return labels["reset"] % stamp
    return labels["reset_left"] % (stamp, fmt_duration(delta, lang))


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
        record = {
            "t": stamp,
            "fable": float(percent),
            "fable_resets_at": entry.get("fable_resets_at"),
        }
        weekly = entry.get("seven_day")
        # seven_day は取れないことがある(API が null を返す)。欠測は None のまま
        # 残し、その指標のパイプラインだけがその点を捨てる。
        record["seven_day"] = None if isinstance(weekly, bool) \
            or not isinstance(weekly, (int, float)) else float(weekly)
        entries.append(record)
    entries.sort(key=lambda e: e["t"])
    return entries


def reset_key(text):
    """Normalize a resets_at string for comparison.

    The API jitters the sub-second part of resets_at on every fetch, so string
    equality would make every sample look like its own window. Compare at
    minute granularity instead.

    That jitter straddles a minute boundary: the same window has been observed
    as both `2026-09-04T15:00:00.051929` and `2026-09-04T14:59:59.579336`.
    Flooring would put those in different minutes (15:00 vs 14:59) and cut the
    window on nearly every fetch, so round to the *nearest* minute. Observed
    jitter is well under a second, so a 30s tolerance on each side is ample.
    """
    dt = parse_iso(text)
    if dt is None:
        return None
    return int((dt.timestamp() + 30) // 60)


# 予測を出す指標。history.jsonl の値のキーと、行のラベルの STRINGS キー。
PROJECTION_METRICS = (("fable", "proj_fable"), ("seven_day", "proj_weekly"))


def metric_entries(entries, field=DEFAULT_METRIC):
    """Drop the samples that have no value for `field` (seven_day can be null)."""
    return [e for e in (entries or [])
            if e.get("t") is not None
            and not isinstance(e.get(field), bool)
            and isinstance(e.get(field), (int, float))]


def window_points(entries, resets_at, field=DEFAULT_METRIC):
    """Keep only the points belonging to the current window.

    Walking backwards from the newest point, stop at the first entry whose
    resets_at differs from the current one, or whose percent is *higher* than
    the point after it -- going forward that is a drop, i.e. a weekly reset.

    The window key is always the entry's `fable_resets_at`: history.jsonl stores
    only that one resets_at, and Fable (...14:59:59.x) and seven_day
    (...15:00:00.x) are the same weekly boundary, which `reset_key`'s
    round-to-nearest-minute maps onto the same key. So the stored field is a
    valid discriminator for both metrics and the schema stays unchanged.
    The %-drop check uses `field`, so each metric detects its own reset.
    """
    points = []
    newer_percent = None
    target = reset_key(resets_at)
    for entry in reversed(metric_entries(entries, field)):
        entry_key = reset_key(entry.get("fable_resets_at"))
        if target is not None and entry_key is not None and entry_key != target:
            break
        if newer_percent is not None and entry[field] > newer_percent:
            break
        points.append(entry)
        newer_percent = entry[field]
    points.reverse()
    return points


def hour_segments(start, end):
    """Split [start, end) at local hour boundaries.

    Yields (local hour-of-day, length in hours). The activity curve is a human
    daily rhythm, so the bucket is the *local* hour, not UTC.
    """
    if start is None or end is None or end <= start:
        return
    cursor = start
    steps = 0
    while cursor < end and steps < CURVE_MAX_STEPS:
        local = cursor.astimezone()
        boundary = local.replace(minute=0, second=0, microsecond=0) \
            + timedelta(hours=1)
        stop = end if end < boundary else boundary
        yield local.hour, (stop - cursor).total_seconds() / 3600.0
        cursor = stop
        steps += 1


def activity_curve(entries, field=DEFAULT_METRIC):
    """24-bucket hour-of-day activity profile, or None when it cannot be built.

    Every pair of consecutive samples inside one window contributes its delta%
    to the hour buckets the pair spans, split proportionally by time. The sums
    are normalized to shares (total 1). Buckets with no observed usage keep a
    share of 0 -- a dead night is information, not missing data.

    Gates: at least CURVE_MIN_SPAN_SECONDS of history overall and a positive
    total delta. Otherwise None (component B is inactive).
    """
    entries = metric_entries(entries, field)
    if len(entries) < 2:
        return None
    if (entries[-1]["t"] - entries[0]["t"]).total_seconds() \
            < CURVE_MIN_SPAN_SECONDS:
        return None
    totals = [0.0] * CURVE_BUCKETS
    for prev, nxt in zip(entries, entries[1:]):
        if reset_key(prev.get("fable_resets_at")) \
                != reset_key(nxt.get("fable_resets_at")):
            continue  # 窓をまたぐ差分は消費ではない。
        delta = nxt[field] - prev[field]
        if delta <= 0:
            continue
        span = (nxt["t"] - prev["t"]).total_seconds() / 3600.0
        if span <= 0:
            continue
        for hour, length in hour_segments(prev["t"], nxt["t"]):
            totals[hour] += delta * (length / span)
    grand = sum(totals)
    if grand <= 0:
        return None
    return [value / grand for value in totals]


def effective_hours(shares, start, end):
    """Wallclock [start, end) re-scaled by how active those hours usually are.

    Sum the shares of the hours covered (prorated), then divide by the mean
    share 1/24. A flat curve gives back the wallclock hours; a curve that is
    dead at night shrinks a night-heavy span towards 0.
    """
    if start is None or end is None or end <= start:
        return 0.0
    if not shares:
        return (end - start).total_seconds() / 3600.0
    total = 0.0
    for hour, length in hour_segments(start, end):
        total += shares[hour] * length
    return total * CURVE_BUCKETS


def advance_effective(shares, start, hours):
    """Wallclock time at which `hours` effective hours have elapsed from start.

    Returns None when the target is not reached within CURVE_MAX_STEPS hours
    (e.g. a curve whose remaining hours are all dead).
    """
    if hours <= 0:
        return start
    if not shares:
        return start + timedelta(hours=hours)
    cursor = start
    remaining = hours
    for _ in range(CURVE_MAX_STEPS):
        local = cursor.astimezone()
        boundary = local.replace(minute=0, second=0, microsecond=0) \
            + timedelta(hours=1)
        length = (boundary - cursor).total_seconds() / 3600.0
        gain = shares[local.hour] * length * CURVE_BUCKETS
        if gain >= remaining > 0:
            rate = shares[local.hour] * CURVE_BUCKETS
            return cursor + timedelta(hours=remaining / rate)
        remaining -= gain
        cursor = boundary
    return None


def weighted_slope(xs, ys, ages_hours, half_life=SLOPE_HALF_LIFE_HOURS):
    """Weighted least squares slope of y over x, weight 0.5 ** (age / half_life).

    Recent samples dominate, so "current pace" follows recent behaviour instead
    of being dragged by the first point of the window. Degenerate input (all x
    equal, zero weights) yields 0.0 rather than an exception.
    """
    sw = sx = sy = sxx = sxy = 0.0
    for x, y, age in zip(xs, ys, ages_hours):
        w = 0.5 ** (max(0.0, age) / half_life) if half_life > 0 else 1.0
        sw += w
        sx += w * x
        sy += w * y
        sxx += w * x * x
        sxy += w * x * y
    denom = sw * sxx - sx * sx
    if sw <= 0 or denom <= 1e-12:
        return 0.0
    return (sw * sxy - sx * sy) / denom


def pace_slope(points, shares=None, field=DEFAULT_METRIC):
    """Component A: %/hour of *effective* time (wallclock when shares is None).

    Timestamps are mapped to effective hours elapsed with the same curve used
    for the remaining time, so slope and remaining time share one unit.
    """
    if len(points) < 2:
        return 0.0
    base = points[0]["t"]
    last = points[-1]["t"]
    xs = [effective_hours(shares, base, p["t"]) for p in points]
    ys = [p[field] for p in points]
    ages = [(last - p["t"]).total_seconds() / 3600.0 for p in points]
    return max(0.0, weighted_slope(xs, ys, ages))


def project(points, resets_at, now=None, shares=None, field=DEFAULT_METRIC):
    """Pace projection for the current window.

    Component A (recency-weighted slope) always; component B (daily activity
    curve) when `shares` is given -- then both the fit and the remaining time
    are measured in effective hours instead of wallclock hours.

    Returns None (not enough data), ("reset", percent) for the value expected at
    the reset, or ("cross", datetime) for when 100% is expected to be reached.
    """
    if len(points) < PROJECTION_MIN_POINTS or resets_at is None:
        return None
    first, last = points[0], points[-1]
    span = (last["t"] - first["t"]).total_seconds()
    if span < PROJECTION_MIN_SPAN_SECONDS:
        return None
    if (resets_at - last["t"]).total_seconds() <= 0:
        return None
    slope = pace_slope(points, shares, field)
    remaining = effective_hours(shares, last["t"], resets_at)
    projected = last[field] + slope * remaining
    projected = max(last[field], min(PROJECTION_MAX_PERCENT, projected))
    # Already past 100%: there is no future crossing to report, just the value.
    if projected > 100 and slope > 0 and last[field] < 100:
        cross = advance_effective(shares, last["t"],
                                  (100.0 - last[field]) / slope)
        if cross is not None:
            return ("cross", cross)
    return ("reset", projected)


COLLECTING_MINUTES_BELOW_SECONDS = 90 * 60
COLLECTING_MINUTE_STEP = 10


def collecting_label(points, now, lang=DEFAULT_LANG):
    """「あと約N時間」/「あと約N分」(en: "~Nh left" / "~Nm left")。

    Time left until the window can be projected. Hours are too coarse near the
    end -- the countdown would sit on 「約1時間」 for a whole hour -- so switch
    to 10-minute steps below 90 minutes.
    """
    labels = strings(lang)
    if not points:
        remaining = PROJECTION_MIN_SPAN_SECONDS
    else:
        remaining = PROJECTION_MIN_SPAN_SECONDS \
            - (now - points[0]["t"]).total_seconds()
    if remaining >= COLLECTING_MINUTES_BELOW_SECONDS:
        return labels["collecting_hours"] % int(math.ceil(remaining / 3600.0))
    minutes = int(math.ceil(max(0.0, remaining) / 60.0))
    step = COLLECTING_MINUTE_STEP
    minutes = max(step, int(math.ceil(minutes / float(step))) * step)
    return labels["collecting_minutes"] % minutes


def projection_result(data, now, history, field):
    """(kind, value) for one metric, or None when its resets_at is unusable.

    kind is "collecting" (value = the current window's points), "reset"
    (value = percent expected at the reset) or "cross" (value = datetime).
    """
    entry = data.get(field)
    if not isinstance(entry, dict):
        return None
    resets_at_text = entry.get("resets_at")
    resets_at = parse_iso(resets_at_text)
    if resets_at is None or (resets_at - now).total_seconds() <= 0:
        return None
    points = window_points(history, resets_at_text, field)
    # 成分Bは履歴全体(全ての窓)から作る。作れなければ None = 成分Aのみ。
    result = project(points, resets_at, now, activity_curve(history, field),
                     field)
    if result is None:
        # 状態は新しいのに履歴が足りないだけ: 収集中であることを出す。
        return ("collecting", points)
    return result


def projection_text(kind, value, lang=DEFAULT_LANG):
    labels = strings(lang)
    if kind == "collecting":
        return None
    if kind == "cross":
        stamp = value.astimezone().strftime(labels["date_cross"])
        # strftime の %p は "PM"。英語表記は "2pm" の形にする。
        stamp = stamp.replace("AM", "am").replace("PM", "pm")
        return labels["cross"] % stamp
    return labels["at_reset"] % int(round(value))


def projection_rows(state, now, history=None, lang=DEFAULT_LANG):
    """The 予測 lines (one per metric), or [] when the state supports none.

    Both metrics are projected the same way; only the metric field and its own
    resets_at differ. When every metric is still collecting, the two rows
    collapse into one 「データ収集中」 row -- the countdown is identical because
    the samples are.
    """
    if not isinstance(state, dict) or state.get("ok") is not True:
        return []
    age = age_seconds(state, now)
    if age is None or age >= STALE_SECONDS:
        return []
    data = state.get("data")
    if not isinstance(data, dict):
        return []
    if history is None:
        history = read_history()

    labels = strings(lang)
    results = []
    for field, key in PROJECTION_METRICS:
        result = projection_result(data, now, history, field)
        if result is not None:
            results.append((labels[key], result))
    if not results:
        return []
    if all(kind == "collecting" for _, (kind, _) in results):
        # 収集中の残り時間は全指標で同じ(点の時刻が同じ)ので 1 行にまとめる。
        points = results[0][1][1]
        return ["%s: %s" % (labels["proj_prefix"],
                            labels["collecting"]
                            % collecting_label(points, now, lang))]
    rows = []
    for label, (kind, value) in results:
        text = projection_text(kind, value, lang) \
            or labels["collecting"] % collecting_label(value, now, lang)
        rows.append("%s%s" % (pad_label(label, lang=lang), text))
    return rows


def limit_rows(data, lang=DEFAULT_LANG):
    """(ラベル, 枠) の並び。`data.scoped` を API の順にそのまま 1 行ずつ出す。

    ラベルは **API が返した名前をそのまま**使う(モデル名は翻訳しない)。
    枠が Fable ひとつだけの通常ケースでは、従来の固定 1 行と同じ出力になる。
    `scoped` を持たない古い state(や壊れた値)では従来どおり `data.fable` を
    1 行だけ出すので、表示が消えることはない。
    """
    labels = strings(lang)
    data = data if isinstance(data, dict) else {}
    rows = []
    scoped = data.get("scoped")
    if isinstance(scoped, list):
        for item in scoped:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name:
                continue
            rows.append((name, item))
    if rows:
        return rows
    return [(labels["label_fable"], data.get("fable"))]


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
           history=None, dark=None, lang=DEFAULT_LANG):
    now = now or datetime.now(timezone.utc).astimezone()
    python_path = python_path or sys.executable or "/usr/bin/python3"
    fetch_path = fetch_path or default_fetch_path()
    labels = strings(lang)

    lines = [title_line(state, now), "---"]

    data = (state or {}).get("data") if isinstance(state, dict) else None
    if isinstance(data, dict):
        rows = limit_rows(data, lang) + [
            (labels["label_weekly"], data.get("seven_day")),
            (labels["label_session"], data.get("five_hour")),
        ]
        if dark is None:
            dark = is_dark_appearance()
        primary = ANSI_PRIMARY_DARK if dark else None
        for label, entry in rows:
            if not isinstance(entry, dict):
                lines.append(ansi_row("%s --" % pad_label(label, lang=lang),
                                      primary, MONO))
                continue
            reset = fmt_reset(entry.get("resets_at"), now, lang)
            text = ("%s %3s%%   %s" % (
                pad_label(label, lang=lang), fmt_percent(entry.get("percent")),
                reset)).rstrip()
            lines.append(ansi_row(text, primary, MONO))
        # 予測行も 3 枠と同じ色・同じ等幅にする(同じ状態表示なので同じ本文色)。
        for projection in projection_rows(state, now, history, lang):
            lines.append(ansi_row(projection, primary, MONO))
        lines.append("---")
        age = age_seconds(state, now)
        fetched = parse_iso(state.get("fetched_at"))
        if fetched is not None:
            stamp = fetched.astimezone().strftime("%H:%M:%S")
            lines.append(labels["fetched"] % (stamp, fmt_duration(age, lang)))
        else:
            lines.append(labels["fetched_none"])
    else:
        lines.append(labels["no_data"])
        lines.append("---")

    if not isinstance(state, dict):
        lines.append(ansi_row(labels["error_no_state"], ANSI_ERROR))
    elif state.get("error"):
        at = parse_iso(state.get("error_at"))
        stamp = at.astimezone().strftime("%H:%M:%S") if at else "?"
        lines.append(ansi_row(labels["error"] % (state.get("error"), stamp),
                              ANSI_ERROR))

    lines.append("---")
    lines.append("%s | bash=%s param1=%s param2=--force terminal=false "
                 "refresh=true sfimage=arrow.clockwise"
                 % (labels["refresh"], python_path, fetch_path))
    lines.append("%s | href=%s sfimage=safari"
                 % (labels["open_usage"], USAGE_PAGE_URL))
    lines.append("%s | bash=/usr/bin/open param1=%s terminal=false "
                 "sfimage=doc.text" % (labels["open_log"], log_path))
    return "\n".join(lines)


def main():
    state = read_state()
    lang = resolve_lang(read_config(), state)
    sys.stdout.write(render(state, lang=lang) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
