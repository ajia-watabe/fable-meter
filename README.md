# fable-meter

> **In short (English).** fable-meter is an **unofficial**, personal macOS menu-bar
> tool that shows your Claude Code usage — in particular the weekly per-model
> quota for **Fable** — which existing meters do not surface. It is **not
> affiliated with, endorsed by, or supported by Anthropic**, and it relies on an
> **undocumented endpoint that may break or disappear at any time**. It reads the
> Claude Code OAuth token from your macOS Keychain, keeps it **in process memory
> only** — never written to any file, log, argument, or environment variable —
> and issues a single **read-only `GET`**, so it consumes **no** usage quota of
> its own. The whole thing is a few hundred lines of dependency-free Python, so
> you can read it yourself before trusting it. Provided as-is under the MIT
> license, with **no support and no guarantees**. Requires macOS, a logged-in
> Claude Code, and Homebrew.

## 読む前に(重要)

### 非公式ツールです

- **Anthropic とは一切関係ありません。** 個人が自分用に作ったもので、公式の承認・提携・支援は受けていません。
- 公開されていない内部エンドポイント(`GET https://api.anthropic.com/api/oauth/usage`)を利用しています。
  **仕様変更や廃止でいつ動かなくなってもおかしくありません。** 壊れたときは黙って古い値を出さず、
  エラーとして表示する設計にしてあります(→ [表示の読み方](#表示の読み方))。

### セキュリティ設計

- Claude Code の OAuth トークンを macOS Keychain(`Claude Code-credentials`)から読み取ります。
- 読み取ったトークンは **プロセス内のメモリ上でのみ** 扱います。
  **ファイル・ログ・stdout / stderr・コマンドライン引数・環境変数・例外メッセージの
  いずれにも一切書き出しません。** `state.json` にも `--dry-run` の出力にも含まれません。
- トークンの送信先は `https://api.anthropic.com` のみです。外部に送る処理はありません。
- キャッシュは `~/.cache/fable-meter/`(ディレクトリ `0700`、`state.json` と `fetch.log` は `0600`)。
- ログに残るのは時刻・結果コード・HTTP ステータス・エラー時のレスポンス本文先頭 300 文字だけです。
- **コードは短く、依存パッケージはゼロ**(Python 3 標準ライブラリのみ)。
  取得層 `fetch.py` と表示層 `plugin/fable.10s.py` を読めば、上記を自分の目で検証できます。
  他人の Keychain を触るツールなので、**信用する前に読んでください。**

### 使用量は消費しません

呼ぶのは読み取り専用の **`GET` 1本だけ**です。推論リクエストは一切発生しないため、
このツール自体が使用量(トークン)を消費することはありません。

### 個人用ツールです

作者が自分のために書いたものを、参考になればと公開しているだけです。
**サポート・動作保証・継続的なメンテナンスの約束はありません。** MIT ライセンス、無保証(as-is)で提供します。
Issue や PR に反応できないことがあります。自己責任でご利用ください。

### 動作要件

- **macOS**(Apple Silicon で確認。launchd / SwiftBar / Keychain に依存するため macOS 専用)
- **Claude Code にログイン済み**であること(Keychain に認証情報があること)
- **Homebrew**(SwiftBar の導入に使用)
- Python 3(macOS 同梱の `/usr/bin/python3` か Homebrew の `python3`)

---

Claude Code の使用量 — 特に **Fable の週次モデル別枠** — を macOS のメニューバーに常時表示する小さなツール。

`ClaudeMeter` などが参照しているエンドポイントは `seven_day_*` の固定キーしか返さず Fable が取れないため、
`GET https://api.anthropic.com/api/oauth/usage` の `limits[]` を直接読む。

```
F13% W10% S9%
 │     │    └ セッション … 5時間枠
 │     └────── 週間      … 週次(全モデル)
 └──────────── Fable     … 週次(Fable スコープ)
```

## 構成

```
launchd (5分ごと)  →  fetch.py  →  ~/.cache/fable-meter/state.json
                                          ↓ 読むだけ
SwiftBar (10秒ごと) →  plugin/fable.10s.py → メニューバー
```

常駐するのは SwiftBar 本体のみ。取得層・表示層とも「起動 → 即終了」でアイドル時のメモリはゼロ。
表示層はネットワークにも Keychain にも触らない。

## インストール

```bash
./install.sh
```

やること:

1. `python3` の絶対パス解決(`/opt/homebrew/bin/python3` 優先)
2. SwiftBar が無ければ `brew install --cask swiftbar`
3. `~/.cache/fable-meter/` を作成
4. 初回取得を前面で実行(ここで Keychain のダイアログが出る → **「常に許可」** を選ぶ)
5. `~/Library/LaunchAgents/com.local.fable-meter.plist` を生成して `launchctl bootstrap gui/$(id -u)`
6. SwiftBar の Plugins ディレクトリにプラグインを配置(shebang を絶対パスに書き換えたコピー)
7. `open -a SwiftBar`

冪等なので何度実行してもよい。

### 確認

```bash
launchctl print gui/$(id -u)/com.local.fable-meter | head
cat ~/.cache/fable-meter/state.json
python3 plugin/fable.10s.py     # 1行目が "F13% W10% S9%" 形式
```

## アンインストール

```bash
./uninstall.sh            # launchd 解除・plist 削除・プラグイン削除
./uninstall.sh --purge    # 上記 + ~/.cache/fable-meter/ も削除
```

SwiftBar 本体は残る(不要なら `brew uninstall --cask swiftbar`)。

## 表示の読み方

| メニューバー | 意味 |
|---|---|
| `F13% W10% S9%` | 正常。取得から10分未満 |
| `F13%? W10%? S9%?` | 取得から 10〜30分。値が古い可能性(グレー表示) |
| `F-- W-- S--` | 取得から30分以上、またはデータ無し(グレー表示。`%` は付かない) |
| `F13%! W10%! S9%!` | 値は新しいが**直近の取得が失敗している**。ドロップダウンに理由 |

色(Fable の % で決まる):

- 80% 超 → 黄 `#e0a800`
- 95% 超 → 赤 `#d0021b`
- 鮮度異常 → グレー `#8e8e93`

**取得に失敗しても直前の値は上書きしない。** 代わりに `!` / `?` / `--` で古さと失敗を明示する
(古い値を「現在値」として誤読させないため)。

ドロップダウンは日本語表示。各枠の % とリセット時刻、プラン、最終取得時刻、エラー、
`今すぐ更新`(手動取得)、`ログを開く` がある。

```
Fable             13%   リセット 9/4 23:59 (あと6日2時間)
週間(全モデル)     10%   リセット 9/4 24:00 (あと6日2時間)
セッション(5h)      9%   リセット 01:00 (あと3時間8分)
---
プラン: max · 取得: 21:50:36 (1分前)
---
今すぐ更新
ログを開く
```

SwiftBar 自身のサブメニュー行は `<swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>` で非表示にしている。

## トラブルシュート

### Keychain のダイアログが毎回出る

初回に「許可」を押すと毎回聞かれる。**「常に許可」** を選ぶこと。
間違えた場合は「キーチェーンアクセス」.app → `Claude Code-credentials` →
情報を見る → アクセス制御 → `security` を許可リストに追加する。

`security find-generic-password -s "Claude Code-credentials" -w` が失敗すると
`keychain_error`、中身の JSON が読めないと `keychain_parse_error` になる。

### `token_expired` と出る

Keychain の `accessToken` が期限切れ(寿命は数時間)。
**本ツールはトークンを自前で更新しない**(Claude Code の認証を壊さないための設計判断)。
Claude Code を起動すれば Claude Code 自身がトークンを更新するので、その後 `今すぐ更新` すれば直る。

### `fable_not_found`

`limits[]` に `kind == "weekly_scoped"` かつ `scope.model.display_name == "Fable"` が無い。
API 側の仕様変更か、Fable 枠が現在割り当てられていない。**0% を捏造せずエラーにしている。**

### `schema_error` / `http_<status>` / `rate_limited` / `network_error` / `auth_error`

`~/.cache/fable-meter/fetch.log` を見る(ドロップダウンの `ログを開く`)。
`auth_error` は 401/403。`rate_limited` は 429 — デバッグ中の連打に注意。

### メニューバーに出ない

```bash
pgrep -fl SwiftBar                                   # 起動しているか
ls "$HOME/Library/Application Support/SwiftBar/Plugins"   # プラグインがあるか
launchctl print gui/$(id -u)/com.local.fable-meter    # 取得が登録されているか
```

SwiftBar の Preferences で Plugin Directory が上記になっているか確認する。

## セキュリティ

OAuth トークンは **プロセス内メモリでのみ** 扱い、
ファイル・ログ・stdout・stderr・コマンドライン引数・環境変数・例外メッセージの
いずれにも出さない。`state.json` にも `--dry-run` の出力にも含まれない。
ログに残るのは時刻・結果コード・HTTP ステータス・エラー時のレスポンス本文先頭 300 文字のみ。

呼ぶのは `GET /api/oauth/usage` の1本だけ。推論は発生しないので使用量は増えない。

## テスト

```bash
python3 -m unittest discover tests
```

## 開発メモ

- Python 3 標準ライブラリのみ。3.9+ 互換(プラグインが `/usr/bin/python3` で走る場合に備える)。
- 設計の詳細は `DESIGN.md`、元の要件は `fable-usage-menubar-spec.md`。

## ライセンス

MIT License — [LICENSE](LICENSE) を参照。無保証(as-is)です。
