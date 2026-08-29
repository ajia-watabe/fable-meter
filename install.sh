#!/bin/bash
# fable-meter installer (idempotent)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.local.fable-meter"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
CACHE="$HOME/.cache/fable-meter"
UID_NUM="$(id -u)"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

# 1. python3
if [ -x /opt/homebrew/bin/python3 ]; then
    PYTHON=/opt/homebrew/bin/python3
else
    PYTHON="$(command -v python3 || true)"
fi
if [ -z "${PYTHON:-}" ]; then
    echo "python3 が見つかりません。" >&2
    exit 1
fi
say "python3: $PYTHON"

# 2. SwiftBar
if [ ! -d /Applications/SwiftBar.app ] && [ ! -d "$HOME/Applications/SwiftBar.app" ]; then
    say "SwiftBar をインストールします (brew install --cask swiftbar)"
    brew install --cask swiftbar
else
    say "SwiftBar は導入済み"
fi

# 3. cache dir
mkdir -p "$CACHE"
chmod 700 "$CACHE"

# 4. initial fetch (foreground -- Keychain dialog appears here)
cat <<'MSG'

--------------------------------------------------------------
初回取得を行います。Keychain のアクセス許可ダイアログが出たら
必ず「常に許可」を選んでください。「許可」だと毎回聞かれます。
--------------------------------------------------------------

MSG
if "$PYTHON" "$REPO/fetch.py" --force --verbose; then
    say "初回取得に成功しました"
else
    echo "初回取得に失敗しました。$CACHE/fetch.log を確認してください。" >&2
fi

# 5. launchd
say "launchd エージェントを登録します"
mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__PYTHON__|$PYTHON|g" \
    -e "s|__REPO__|$REPO|g" \
    -e "s|__HOME__|$HOME|g" \
    "$REPO/launchd/$LABEL.plist.template" > "$PLIST"
launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NUM" "$PLIST"

# 6. plugin
PLUGIN_DIR="$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || true)"
if [ -z "$PLUGIN_DIR" ]; then
    PLUGIN_DIR="$HOME/Library/Application Support/SwiftBar/Plugins"
    defaults write com.ameba.SwiftBar PluginDirectory -string "$PLUGIN_DIR"
fi
mkdir -p "$PLUGIN_DIR"
DEST="$PLUGIN_DIR/fable.10s.py"
# shebang を絶対パスに書き換えたコピーを置く(SwiftBar が PATH を継承しない環境向け)
rm -f "$DEST"
{
    printf '#!%s\n' "$PYTHON"
    tail -n +2 "$REPO/plugin/fable.10s.py" \
        | sed -e "s|^FETCH_PATH = \"\"$|FETCH_PATH = \"$REPO/fetch.py\"|"
} > "$DEST"
chmod +x "$DEST"
say "プラグインを配置しました: $DEST"

# 7. start SwiftBar
open -a SwiftBar || true

# 8. how to verify
cat <<MSG

インストール完了。確認方法:
  launchctl print gui/$UID_NUM/$LABEL | head
  cat $CACHE/state.json
  $PYTHON $REPO/plugin/fable.10s.py

MSG
