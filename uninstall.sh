#!/bin/bash
# fable-meter uninstaller. --purge でキャッシュも消す。
set -euo pipefail

LABEL="com.local.fable-meter"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
CACHE="$HOME/.cache/fable-meter"
UID_NUM="$(id -u)"
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

say "launchd エージェントを解除します"
launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
rm -f "$PLIST"

PLUGIN_DIR="$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || true)"
[ -z "$PLUGIN_DIR" ] && PLUGIN_DIR="$HOME/Library/Application Support/SwiftBar/Plugins"
if [ -e "$PLUGIN_DIR/fable.10s.py" ]; then
    rm -f "$PLUGIN_DIR/fable.10s.py"
    say "プラグインを削除しました"
fi

if [ "$PURGE" -eq 1 ]; then
    rm -rf "$CACHE"
    say "キャッシュを削除しました: $CACHE"
else
    say "キャッシュは残しています(削除するには --purge): $CACHE"
fi

say "完了。SwiftBar 自体は残っています(不要なら brew uninstall --cask swiftbar)。"
