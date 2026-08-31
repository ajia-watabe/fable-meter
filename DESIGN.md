# fable-meter — 設計書

作成: 2026-08-29。元の要件は `fable-usage-menubar-spec.md`。本書はヒアリングと調査で確定した設計を記す。

## 0. ヒアリングで確定した決定

| 項目 | 決定 |
|---|---|
| データ取得経路 | **経路A**(HTTP API)。経路B(PTY で `/usage`)は不要 |
| 取得層の言語 | Python 3(標準ライブラリのみ。`/opt/homebrew/bin/python3` 3.14) |
| 表示層 | SwiftBar プラグイン(Python) |
| トークン期限切れ時 | **自前更新しない**。エラー表示に留める(Claude Code の認証を壊すリスクをゼロにする) |
| 通知 | Fable の 80% / 95% 到達時に macOS 通知を1回ずつ。ほかはメニューバーの色変化のみ |
| メニューバー表示 | 短縮形 `F12% W9% S6%` |
| 取得間隔 | 5分(launchd)+ ドロップダウンの `Refresh now` |
| プロジェクト名 | `fable-meter`(キャッシュ `~/.cache/fable-meter/`、launchd ラベル `com.local.fable-meter`) |
| リポジトリ | git 管理、GitHub 公開(MIT)。当初は private 前提だった |

## 1. 調査結果(2026-08-29 実測)

### 使うエンドポイント

```
GET https://api.anthropic.com/api/oauth/usage
Authorization: Bearer <Claude Code の OAuth accessToken>
anthropic-beta: oauth-2025-04-20
Accept: application/json
```

- org UUID は**不要**(URL に含まれない)。
- claude.ai 側の `/api/organizations/<org>/usage` は同じ形を返すが、OAuth トークンを拒否する(403 `oauth_token_not_accepted`)ので使わない。

### レスポンスの要点

```json
{
  "five_hour": {"utilization": 6.0, "resets_at": "2026-08-29T16:00:00+00:00", ...},
  "seven_day": {"utilization": 9.0, "resets_at": "2026-09-04T15:00:00+00:00", ...},
  "seven_day_opus": null, "seven_day_sonnet": null, "...": null,
  "limits": [
    {"kind": "session",       "group": "session", "percent": 6,  "severity": "normal", "resets_at": "...", "scope": null, "is_active": false},
    {"kind": "weekly_all",    "group": "weekly",  "percent": 9,  "severity": "normal", "resets_at": "...", "scope": null, "is_active": false},
    {"kind": "weekly_scoped", "group": "weekly",  "percent": 12, "severity": "normal", "resets_at": "...",
     "scope": {"model": {"id": null, "display_name": "Fable"}, "surface": null}, "is_active": true}
  ],
  "extra_usage": {...}, "spend": ..., "member_dashboard_available": ...
}
```

**Fable は `limits[]` の中の `kind == "weekly_scoped"` かつ `scope.model.display_name == "Fable"` の要素。** claude-meter が `null` しか出せないのは、固定キー `seven_day_*` だけを見て `limits[]` を無視しているため。

### 認証情報

Keychain の `Claude Code-credentials`(generic password)の値は JSON:

```
{"claudeAiOauth": {"accessToken", "refreshToken", "expiresAt"(ms epoch), "refreshTokenExpiresAt", "scopes", "subscriptionType", "rateLimitTier"}, "mcpOAuth": {...}}
```

- `accessToken` の寿命は数時間。Claude Code が動いている間は Claude Code 自身が更新する。
- `expiresAt` を見て期限切れならAPIを呼ばずに `token_expired` エラーにする。

## 2. アーキテクチャ

```
launchd (5分ごと, RunAtLoad)
  └─ /opt/homebrew/bin/python3 <repo>/fetch.py
       ├─ Keychain からトークン取得(プロセス内メモリのみ)
       ├─ GET api.anthropic.com/api/oauth/usage
       ├─ limits[] をパース
       ├─ ~/.cache/fable-meter/state.json を atomic に書く
       ├─ ~/.cache/fable-meter/history.jsonl に1行追記(8日より古い行は間引く)
       └─ 80% / 95% を跨いだら osascript で通知 → 終了

SwiftBar (10秒ごと)
  └─ ~/Library/Application Support/SwiftBar/Plugins/fable.10s.py
       ├─ state.json と history.jsonl を読む(ネットワークも Keychain も触らない)
       └─ 1行 + ドロップダウン(ペース予測を含む)を stdout に出す → 終了
```

常駐するのは SwiftBar 本体のみ。取得層・表示層ともに「起動 → 即終了」。

## 3. リポジトリ構成

```
claude-usage-tracker/          (GitHub: fable-meter)
├── README.md                  使い方・インストール・アンインストール・トラブルシュート
├── DESIGN.md                  本書
├── fable-usage-menubar-spec.md 元の要件(org_uuid の記載は削除する)
├── fetch.py                   取得層
├── plugin/fable.10s.py        SwiftBar プラグイン(表示層)
├── launchd/com.local.fable-meter.plist.template  絶対パスを install.sh が埋める
├── install.sh                 SwiftBar 導入・plist 生成と登録・プラグイン配置・初回取得
├── uninstall.sh               上記を完全に元に戻す
├── tests/
│   ├── fixtures/usage_response.json   §1 の形のサンプル(実値は伏せる)
│   ├── test_fetch.py
│   └── test_plugin.py
└── .gitignore                 __pycache__, *.log, state.json 等
```

## 4. 取得層 `fetch.py`

### 入出力

- 引数: `--dry-run`(state.json を書かず JSON を stdout へ)、`--verbose`(ログを stderr にも)、`--force`(スリープ復帰スキップを無視)
- 出力: `~/.cache/fable-meter/state.json`(atomic: `state.json.tmp` に書いて `os.replace`)、
  `~/.cache/fable-meter/history.jsonl`(0600、追記)
- ログ: `~/.cache/fable-meter/fetch.log`(200KB でローテート、1世代)
- 終了コード: 0 成功 / 1 取得失敗(state.json はエラー状態で更新済み) / 2 引数エラー

### 処理

1. スリープ復帰判定: `sysctl -n kern.waketime` の秒が現在時刻から 60 秒以内なら**1回スキップ**(`--force` で無視)。state.json は更新しない。
2. Keychain 読み取り: `security find-generic-password -s "Claude Code-credentials" -w`。失敗 → `keychain_error`。JSON 不正 → `keychain_parse_error`。
3. `claudeAiOauth.expiresAt` が現在時刻以下 → API を呼ばずに `token_expired`。
4. HTTP GET(timeout 15s)。
   - 401/403 → `auth_error`(本文の `error.type` を含める)
   - 429 → `rate_limited`
   - その他非 200 → `http_<status>`
   - ネットワーク例外 → `network_error`
5. パース:
   - `limits[]` が無い/配列でない → `schema_error`
   - `five_hour` ← `kind == "session"`、`seven_day` ← `kind == "weekly_all"`。無ければトップレベルの `five_hour` / `seven_day` にフォールバック。
   - `scoped[]` ← `kind == "weekly_scoped"` を全て(`name = scope.model.display_name or scope.surface or "unknown"`)。
   - `fable` ← `scoped` のうち `name == "Fable"`(大文字小文字無視)。**無ければ `fable_not_found` エラー。0% を捏造しない。**
   - `percent` が数値でない要素は `schema_error`。
6. state.json 書き出し。
7. history.jsonl に1行追記(§4.1)。
8. 閾値通知の判定と発火(§4.2)。`--dry-run` では 6〜8 をいずれも行わない(副作用なし)。

### state.json スキーマ(schema 1)

```json
{
  "schema": 1,
  "ok": true,
  "fetched_at": "2026-08-29T21:05:12+09:00",
  "error": null,
  "error_at": null,
  "data": {
    "fable":     {"percent": 12, "resets_at": "2026-09-04T14:59:59+00:00", "severity": "normal"},
    "seven_day": {"percent": 9,  "resets_at": "...", "severity": "normal"},
    "five_hour": {"percent": 6,  "resets_at": "...", "severity": "normal"},
    "scoped":    [{"name": "Fable", "percent": 12, "resets_at": "...", "severity": "normal"}],
    "plan": "max"
  },
  "last_notified_band": 0
}
```

`last_notified_band` は閾値通知の状態(0 = 平常 / 1 = 80% 済 / 2 = 95% 済)。失敗時も直前の値を保持する。
壊れた値(非 int、範囲外)は 0 として扱う。

### history.jsonl(ペース予測用)

`~/.cache/fable-meter/history.jsonl`(0600)。取得成功のたびに1行 JSON を**追記**する:

```json
{"t": "2026-08-29T21:05:12+00:00", "fable": 12, "seven_day": 9, "fable_resets_at": "2026-09-04T14:59:59+00:00"}
```

- `t` は UTC の ISO8601。`fable` / `seven_day` は整数パーセント(取れなければ `seven_day` は `null`)。
- 書き込みのたびに 8 日より古い行を間引く。落ちる行があるときだけ `.tmp` + `os.replace` で書き直し、
  通常は純粋な追記で済ませる。
- 書き手は launchd の1プロセスのみ。手動リフレッシュと衝突しても最悪1行重複するだけで、
  予測は重複に耐える(同一時刻の点が増えても傾きは変わらない)。

### 4.2 閾値通知

- バンド: `0` (<80%) / `1` (>=80%) / `2` (>=95%)。
- 直前の `last_notified_band` と比較し、**増加したときだけ** 1 回通知する。
- 週次リセットの検出(前回より Fable % が**減った**、または `resets_at` が **60 秒より大きく前進した**)で
  バンドを 0 に戻す。60 秒の下限は API が返す `resets_at` の秒未満の揺れを誤検出しないため。
- 発火は `/usr/bin/osascript -e 'display notification "..." with title "fable-meter"'`。
  文字列に差し込むのは**整数パーセントのみ**(閾値と現在値)。ユーザー入力や API 文字列は一切入れない。
- 失敗中(`ok=false`)の取得では通知しない(そもそも成功パスでしか呼ばない)。

失敗時: `ok=false`、`error`(コード文字列)と `error_at` をセットし、**`data` と `fetched_at` は直前の成功値をそのまま保持する**。表示層はこの `fetched_at` で鮮度を判定する。一度も成功していなければ `data=null, fetched_at=null`。

### セキュリティ規約(必須)

- トークンを**ファイル・ログ・stdout・stderr・コマンドライン引数・環境変数・例外メッセージのいずれにも出さない**。
- ログに書くのは: 時刻、結果コード、HTTP ステータス、レスポンス本文の先頭 300 文字(エラー時のみ)。リクエストヘッダは書かない。
- `--dry-run` の出力にもトークンは含めない(state.json と同じ形)。

## 5. 表示層 `plugin/fable.10s.py`

- ドロップダウンの状態表示行(3枠・予測・取得・エラー・データ無し)には **`color=` を付けない**。
  SwiftBar の `MenuBarItem.configureAction()` は
  `if params.hasAction || params.color != nil { item.target = self; item.action = ... }` なので、
  `color=` を付けただけの行もクリック可能な項目になり、ホバーで選択ハイライトが出る
  (SwiftBar に `disabled=` 相当のパラメータは無い)。状態は状態として扱うため、
  色より「選択できないこと」を優先し、macOS 標準の無効項目描画(淡色)に任せる。
  エラー行の赤の代わりに、先頭に ⚠️ を付けて目立たせる。
- SwiftBar メタデータ(`<swiftbar.hideAbout>`, `<swiftbar.runInBash>false` 等)を先頭コメントに。SwiftBar 自身のサブメニュー行は `<swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>` で隠す。
- state.json を読む。無い/壊れている → `F-- W-- S--` と、ドロップダウンにエラー。
- 鮮度 = `now - fetched_at`:

| 条件 | タイトル |
|---|---|
| 正常(10分未満) | `F12% W9% S6%` |
| 10分以上 30分未満 | `F12%? W9%? S6%?` |
| 30分以上、または `data == null` | `F-- W-- S--`(データが無いので `%` は付けない) |
| `ok == false` かつ鮮度は正常/10分未満 | `F12%! W9%! S6%!`(値は残すが失敗中であることを示す) |

- 色(Fable の % で決める。SwiftBar は背景色を変えられないので文字色で代替): 80% 超 → `color=#e0a800`(黄)、95% 超 → `color=#d0021b`(赤)。それ以外はデフォルト。鮮度異常(`?`/`--`)時はグレー `color=#8e8e93`。
- ドロップダウン(日本語。ラベルは全角幅を2桁として計算し、等幅 `font=Menlo size=12` で桁を揃える):
  ```
  Fable             12%   リセット 9/4 23:59 (あと5日2時間)
  週間(全モデル)      9%   リセット 9/4 24:00 (あと5日3時間)
  セッション(5h)      6%   リセット 01:00 (あと3時間21分)
  予測: リセット時点 ~34%              ← 履歴が足りなければ「データ収集中(あと約2時間)」
  ---
  取得: 21:05:12 (3分前)
  ⚠️ エラー: token_expired (21:10:00)  ← エラー時のみ
  ---
  リフレッシュ      | bash=<abs python3> param1=<abs fetch.py> param2=--force terminal=false refresh=true sfimage=arrow.clockwise
  使用量ページを開く | href=https://claude.ai/settings/usage sfimage=safari
  ログを開く        | bash=open param1=~/.cache/fable-meter/fetch.log terminal=false sfimage=doc.text
  ```

### ペース予測(`予測:` 行)

1. `history.jsonl` を読み、**現在の窓の点だけ**を取る。新しい順に遡り、`fable_resets_at` が
   現在の `resets_at` と違う点、または(時系列で見て)% が下がる直前の点で打ち切る。
   **`resets_at` はフェッチごとに秒未満が揺れる**(実測: `...15:00:00.051929` → `...15:00:00.487294`)ため、
   文字列一致ではなく**分単位に丸めて**比較する。同じ理由で §4.2 のリセット判定も 60 秒以下の前進は無視する。
   % の低下はリセットが起きた証拠なので、それより前は捨てる。
2. 点が 2 個未満、最古と最新の間隔が 3 時間未満、`resets_at` が不明/過去 → **何も出さない**。
3. 傾き `slope = (最新% - 最古%) / 間隔` を取り、`projected = 最新% + slope * (resets_at - 最新t)`。
   `[最新%, 200]` にクランプする。
4. `projected <= 100` → `予測: リセット時点 ~34%`。
   `projected > 100`(かつ最新 % が 100 未満)→ 100% に到達する時刻を逆算して
   `予測: 100%到達 9/3 15時ごろ`。最新 % が既に 100 以上なら到達時刻は出さず値だけ出す。
5. `ok != true`、または鮮度 10 分以上のときは**出さない**(古い/失敗中の状態に基づく予測はしない)。
   `resets_at` が不明/過去のときも出さない。
6. 状態が新しく `ok` なのに 2 の理由で外挿できないときは、行を消さずに
   `予測: データ収集中(あと約N時間)` を出す。`N = ceil((3時間 - (now - 今の枠の最古の点)) / 1時間)`
   で下限 1、点が1つも無ければ 3。「何も出ない」と「まだ足りない」を区別するため。
7. 色は付けない(下記の理由)。
- リセット時刻は `astimezone()` でローカル時刻に変換して表示する。
- 表示層は**ネットワークにも Keychain にもアクセスしない**。
- 実行間隔はファイル名 `fable.10s.py` で固定。

## 6. launchd

`launchd/com.local.fable-meter.plist.template`(install.sh が `__PYTHON__`, `__REPO__`, `__HOME__` を置換):

```xml
Label: com.local.fable-meter
ProgramArguments: [__PYTHON__, __REPO__/fetch.py]
StartInterval: 300
RunAtLoad: true
StandardOutPath / StandardErrorPath: __HOME__/.cache/fable-meter/launchd.out.log / launchd.err.log
EnvironmentVariables: PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
```

登録は `launchctl bootstrap gui/$(id -u) <plist>`、解除は `launchctl bootout gui/$(id -u)/com.local.fable-meter`。

## 7. install.sh / uninstall.sh

install.sh(冪等):
1. `python3` の絶対パス解決(`/opt/homebrew/bin/python3` 優先、無ければ `command -v python3`)。
2. SwiftBar が無ければ `brew install --cask swiftbar`。
3. `~/.cache/fable-meter/` 作成。
4. **初回取得を前面で実行**(`fetch.py --force --verbose`)。ここで Keychain ダイアログが出る → 「常に許可」を案内するメッセージを事前に表示。
5. plist をテンプレートから生成して `~/Library/LaunchAgents/` に置き、既存があれば bootout してから bootstrap。
6. プラグインを SwiftBar の Plugins ディレクトリ(既定 `~/Library/Application Support/SwiftBar/Plugins/`、`defaults read com.ameba.SwiftBar PluginDirectory` があればそれ)に**シンボリックリンク**。shebang は `#!/usr/bin/env python3`。SwiftBar が PATH を継承しない環境向けに、install.sh はプラグイン先頭の shebang を絶対パスに書き換えたコピーを置く方式でもよい(どちらかに統一)。
7. SwiftBar を起動(`open -a SwiftBar`)。
8. 結果の確認方法を表示。

uninstall.sh: bootout → plist 削除 → プラグイン削除 → キャッシュ削除は `--purge` 指定時のみ
(`--purge` は `~/.cache/fable-meter/` ごと消すので `history.jsonl` も消える)。

## 8. テスト(`python3 -m unittest`)

- `test_fetch.py`: fixture から `parse_usage()` が fable/seven_day/five_hour を正しく取り出す。`limits[]` から Fable を抜いた fixture → `fable_not_found`。`limits` 欠落 → `schema_error`。`percent` 非数値 → `schema_error`。state 書き出しがエラー時に前回の `data` を保持する。
- `test_fetch.py`(追加): history のレコード生成・8日プルーニング・0600 追記、
  バンド判定(80/95 の跨ぎで1回だけ、% 低下や `resets_at` 前進でバンドが 0 に戻る、壊れた値は 0)、
  通知文字列に整数以外が入らないこと。
- `test_plugin.py`: 鮮度 0/11/31 分で `F12% W9% S6%` / `F12%? …` / `F-- …` になる。`ok=false` で `!`。80%/95% 超で色パラメータが付く。state.json 欠落で `--`。日本語ラベル・`リセット` / `リフレッシュ` / `使用量ページを開く` / `ログを開く` が出る。
- `test_plugin.py`(予測): 通常の線形予測、窓の跨ぎ(リセットを挟むと古い点を捨てる)、
  データ不足で行が出ない、100% 超で到達時刻に切り替わる、失敗中/古い state では出さない。

## 9. 動作確認手順(実装後に必ず行う)

1. `python3 fetch.py --dry-run --force` → JSON に `fable.percent` が出て、claude.ai 設定画面の値と一致する。
2. `python3 -m unittest discover tests` 全件パス。
3. `./install.sh` → `launchctl print gui/$(id -u)/com.local.fable-meter` で登録確認、`~/.cache/fable-meter/state.json` が生成される。
4. `python3 plugin/fable.10s.py` を直接実行して 1 行目が `F12% W9% S6%` 形式であることを確認。SwiftBar のメニューバーにも出る。
5. トークン消費確認: 取得を 3 回連続で実行しても `percent` が変わらない(GET のみなので推論は発生しない)。
6. ログ・state.json・`ps` の引数に `accessToken` 文字列が含まれないことを `grep` で確認。

## 10. 既知のリスクと対応(仕様書 §5 に追加)

| リスク | 対応 |
|---|---|
| トークン漏洩 | §4 セキュリティ規約。`user:inference` スコープ付きなので漏れると使用量を食われる |
| 古い値の誤認 | `fetched_at` による `?` / `--` 表示。失敗時も `data` は上書きしない |
| Keychain「常に許可」 | `security` コマンドに対する許可。元々 Keychain にある情報なので新規リスクは小さい |
| 非公開 API の変更 | `schema_error` / `fable_not_found` を握り潰さず `!` を出す |
| launchd の PATH | plist に PATH を明示、python3 は絶対パス |
| 9/1 プロモ終了 | 同じ作業量で % が跳ねる。異常ではない。跳ねた結果 80%/95% を跨ぐと通知が出ることがある |
| 予測の外れ | 直線外挿にすぎない。データ不足なら出さない・失敗中や古い state では出さないことで「それらしい嘘」を避ける |
