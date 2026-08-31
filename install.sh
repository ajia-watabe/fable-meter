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
# テンプレート置換は python3 に任せる(パスに | & < > " が含まれても壊れないよう
# sed のメタ文字ではなく固定文字列置換 + XML エスケープを使う)。
"$PYTHON" - "$REPO/launchd/$LABEL.plist.template" "$PLIST" "$PYTHON" "$REPO" "$HOME" <<'PYEOF'
import sys
from xml.sax.saxutils import escape
src, dst, python, repo, home = sys.argv[1:6]
text = open(src, encoding="utf-8").read()
for key, value in (("__PYTHON__", python), ("__REPO__", repo), ("__HOME__", home)):
    text = text.replace(key, escape(value))
open(dst, "w", encoding="utf-8").write(text)
PYEOF
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
# shebang と FETCH_PATH の埋め込みも python3 で行う(パスをそのまま Python の
# 文字列リテラルへ差し込むと任意コード混入になりうるため repr() で安全に囲む)。
"$PYTHON" - "$REPO/plugin/fable.10s.py" "$DEST" "$PYTHON" "$REPO/fetch.py" <<'PYEOF'
import os, sys
src, dst, python, fetch = sys.argv[1:5]
lines = open(src, encoding="utf-8").read().splitlines(True)
lines[0] = "#!" + python + "\n"
out = []
for line in lines:
    if line.rstrip("\n") == 'FETCH_PATH = ""':
        line = "FETCH_PATH = %r\n" % fetch
    out.append(line)
fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o755)
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    fh.write("".join(out))
PYEOF
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
