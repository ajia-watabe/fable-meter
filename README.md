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
> Claude Code, and Homebrew. The menu follows your system language (Japanese or
> English); set `lang` in `~/.config/fable-meter/config.json` to force one.

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
- キャッシュは `~/.cache/fable-meter/`(ディレクトリ `0700`、`state.json` / `fetch.log` /
  `history.jsonl` は `0600`)。履歴に入るのは時刻とパーセントだけです。
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
                          │                history.jsonl(予測用の履歴)
                          └─ 80%/95% 到達で macOS 通知
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

## 設定

`~/.config/fable-meter/config.json`(**キャッシュとは別の場所**。`./uninstall.sh --purge` でも消えない)。
無ければ取得時に既定値で自動生成する(ディレクトリ `0700`、ファイル `0600`)。
普段は**手で編集する**ファイルで、書き換えは次の描画(最大10秒)で反映される。

```json
{
  "lang": "auto"
}
```

| `lang` | 表示 |
|---|---|
| `"auto"`(既定) | システムのロケール(`defaults read -g AppleLocale`)が `ja` なら日本語、それ以外は英語 |
| `"ja"` | 常に日本語 |
| `"en"` | 常に英語 |

ファイルが無い・壊れている・知らない値のときは `auto` として扱う(設定ミスで表示が止まることはない)。
ロケールの判定は取得層(`fetch.py`)が5分ごとに1回だけ行い、結果を `state.json` の `locale_lang` に置く。
SwiftBar がプラグインに渡す環境には `LANG` が無いことが多く、表示層だけでは判定できないため。

英語表示のドロップダウン:

```
Fable                  13%   resets Sep 4 23:59 (6d 2h left)
Weekly (all models)    10%   resets Sep 5 00:00 (6d 2h left)
Session (5h)            9%   resets 01:00 (3h 8m left)
Forecast Fable       ~34% at reset
Forecast Weekly      ~27% at reset
---
fetched: 21:50:36 (1m ago)
---
Refresh
Open usage page
Open log
```

macOS 通知(80% / 95%)も同じ設定に従う。

## アンインストール

```bash
./uninstall.sh            # launchd 解除・plist 削除・プラグイン削除
./uninstall.sh --purge    # 上記 + ~/.cache/fable-meter/ も削除
```

`--purge` は `state.json` / `fetch.log` に加えて、予測用の履歴 `history.jsonl` も消す。
設定 `~/.config/fable-meter/config.json` は**キャッシュではないので残る**(→ [設定](#設定))。

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

ドロップダウンは日本語または英語(→ [設定](#設定))。各枠の % とリセット時刻、ペース予測、最終取得時刻、エラー、
`リフレッシュ`(手動取得)、`使用量ページを開く`、`ログを開く` がある。

```
Fable             13%   リセット 9/4 23:59 (あと6日2時間)
週間(全モデル)     10%   リセット 9/4 24:00 (あと6日2時間)
セッション(5h)      9%   リセット 01:00 (あと3時間8分)
予測 Fable      リセット時点 ~34%
予測 週間       リセット時点 ~27%
---
取得: 21:50:36 (1分前)
---
リフレッシュ
使用量ページを開く
ログを開く
```

`予測` 行は **Fable** と **週間(全モデル)** の 2 本。上の枠と同じ順で、ラベルの桁も揃えてある。
どちらも、**直近のペース**(新しいサンプルほど重く見る)と、**1日のうちどの時間帯に
使っているかのパターン**(履歴から数日かけて学習する)の 2 つから計算している。
そのため使い始めは「直近のペースをそのまま残り時間に掛けた」粗い見積りで、
履歴が 24 時間分を超えたあたりから「夜は使わない」といった生活パターンを織り込んだ値に落ち着く。
使うほど安定する。履歴が足りないうちは `予測: データ収集中(あと約2時間)` の 1 行にまとまる
(足りない値をそれらしく見せることはしない)。

上半分は**状態表示**なので、クリックできない項目として出す。操作できるのは下の 3 行だけ。

SwiftBar は `color=` を付けた行を自動的にクリック可能な項目に変えてしまう
(`buildMenuItem()`: `let needsAction = params.hasAction || params.color != nil`、
SwiftBar v2.1.1 `SwiftBar/MenuBar/MenuBarItem.swift:1518`)。
そこで状態表示の行は `color=` を使わず、**`ansi=true` + ANSI エスケープ**で色を付けている。
`ansi` は `hasAction` にも `color` にも影響しないので、行は**無効項目のまま色が付く**
(実測: 開いたメニューを Accessibility で読むと状態表示行は `enabled=false`、
操作行 3 つだけが `enabled=true`)。

**ただし macOS 26(Tahoe)では、無効項目でもポインタを載せると紫のハイライトが描かれる。**
これは AppKit 側の描画で、`color=` を外しても `ansi` にしても、`取得:` のように
色を一切付けていない行でも同じように出る(macOS 26.3 / SwiftBar 2.1.1 で全行を実測)。
クリックは効かない(無効項目のまま)が、ハイライト自体はプラグイン側からも
SwiftBar 側からも消せない。SwiftBar の最新リリースも未リリースの main も
この判定ロジックは同じなので、アップグレードでも解決しない。

- 3 枠の行: ダークのとき ANSI 256 色 189(ほぼ白)で本文色に上げる。
  ライトは ANSI パレットに「黒に近い有彩色」が無いのでデフォルトのまま。
- `予測` 行: 3 枠と同じ扱い(ダークは同じ ANSI 189、ライトは無色、等幅 `font=Menlo`)。
  同じ状態表示なので同じ本文色にしている。クリックはできない。
- `取得:` 行: 色を付けない(macOS 標準の淡色 = 副次情報)。
- エラー行: ANSI 31(`NSColor.systemRed`、ライト/ダーク自動)で赤。⚠️ の代用は不要になったので外した。

なお無効項目でも AppKit は**有彩色**の `attributedTitle` をそのまま描くが、
r==g==b の**無彩色**(グレー・白・黒)は無効項目用の淡色に差し替えられる。
ANSI 256 色のグレースケール段(232-255)が使えないのはこのため。

### 予測(ペース予測)

`~/.cache/fable-meter/history.jsonl`(取得のたびに1行追記、8日で間引き)から、
**今の週次枠の中だけ**の増え方を直線で外挿し、リセット時点の % を出す。
**Fable と 週間(全モデル)** の両方について、同じ計算を**それぞれの値**で行う
(履歴には最初から両方の % が入っているので、追加の取得は要らない)。

- `予測 Fable      リセット時点 ~34%` … このペースならリセット時点で約 34%
- `予測 週間       100%到達 9/3 15時ごろ` … このペースだとリセット前に使い切る見込み
- `予測: データ収集中(あと約2時間)` … まだ外挿できるだけの履歴が無い。あと何時間で出るかの目安
  (両方とも収集中のときは1行にまとめる。サンプルの時刻が同じなので残り時間も同じ)

外挿には、今の枠の中に点が2個以上あり、その最古と最新が3時間以上離れている必要がある。
足りないうちは「データ収集中」を出す(履歴が育つのを待っているだけで、壊れてはいない)。
なお **macOS 通知は Fable のみ**(80% / 95%)で、週間の予測は表示だけ。
取得が失敗中(`!`)や値が古い(`?` / `--`)ときは**行ごと出さない**。
古い値や失敗した状態からそれらしい予測を作らないための設計。

途中で週次リセットを跨いだ場合は、% が下がった点より前を捨てて今の枠だけを見る。

あくまで**直線外挿**なので、使い方が変われば当然外れる。目安として見ること。

### 通知

Fable が **80%** と **95%** を超えたとき、macOS の通知を **各1回だけ** 出す。

- 同じ週次枠の中では同じ閾値で二度は鳴らない。リセット(% の低下、またはリセット時刻の前進)を
  検出したら状態を戻すので、次の週にはまた鳴る。
- 取得に失敗しているときは鳴らさない。
- **9/1 のような制限変更の直後は、同じ作業量でも % が跳ねる。** その結果として閾値を跨ぎ、
  通知が出ることがある。ツールの異常ではない。
- 通知を止めたい場合は「システム設定 → 通知」で `osascript`(スクリプトエディタ)の通知をオフにする。

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
Claude Code を起動すれば Claude Code 自身がトークンを更新するので、その後 `リフレッシュ` すれば直る。

### `fable_not_found`

`limits[]` に `kind == "weekly_scoped"` かつ `scope.model.display_name == "Fable"` が無い。
名前は完全一致だけでなく **`Fable` で始まる名前**(将来の `Fable 5.1` など)も拾うので、
モデル名が改称されただけなら追随する(複数該当時は `is_active` → `percent` 最大の順に選び、
選んだ名前を `state.json` の `data.fable.name` に記録する)。
それでも当たらないのは API 側の仕様変更か、Fable 枠が現在割り当てられていない場合。
**0% を捏造せずエラーにしている。**

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
