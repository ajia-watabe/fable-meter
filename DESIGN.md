# fable-meter — 設計書

作成: 2026-08-29。元の要件は `fable-usage-menubar-spec.md`。本書はヒアリングと調査で確定した設計を記す。

## 0. ヒアリングで確定した決定

| 項目 | 決定 |
|---|---|
| データ取得経路 | **経路A**(HTTP API)。経路B(PTY で `/usage`)は不要 |
| 取得層の言語 | Python 3(標準ライブラリのみ。`/opt/homebrew/bin/python3` 3.14) |
| 表示層 | SwiftBar プラグイン(Python) |
| トークン期限切れ時 | **自前更新しない**。エラー表示に留める(Claude Code の認証を壊すリスクをゼロにする) |
| 通知 | なし。メニューバーの色変化のみ |
| メニューバー表示 | 短縮形 `F12% W9% S6%` |
| 取得間隔 | 5分(launchd)+ ドロップダウンの `Refresh now` |
| プロジェクト名 | `fable-meter`(キャッシュ `~/.cache/fable-meter/`、launchd ラベル `com.local.fable-meter`) |
| リポジトリ | git 管理、GitHub **private** |

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
       └─ ~/.cache/fable-meter/state.json を atomic に書く → 終了

SwiftBar (10秒ごと)
  └─ ~/Library/Application Support/SwiftBar/Plugins/fable.10s.py
       ├─ state.json を読む(ネットワークも Keychain も触らない)
       └─ 1行 + ドロップダウンを stdout に出す → 終了
```

常駐するのは SwiftBar 本体のみ。取得層・表示層ともに「起動 → 即終了」。

## 3. リポジトリ構成

```
claude-usage-tracker/          (GitHub: fable-meter, private)
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
- 出力: `~/.cache/fable-meter/state.json`(atomic: `state.json.tmp` に書いて `os.replace`)
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
  }
}
```

失敗時: `ok=false`、`error`(コード文字列)と `error_at` をセットし、**`data` と `fetched_at` は直前の成功値をそのまま保持する**。表示層はこの `fetched_at` で鮮度を判定する。一度も成功していなければ `data=null, fetched_at=null`。

### セキュリティ規約(必須)

- トークンを**ファイル・ログ・stdout・stderr・コマンドライン引数・環境変数・例外メッセージのいずれにも出さない**。
- ログに書くのは: 時刻、結果コード、HTTP ステータス、レスポンス本文の先頭 300 文字(エラー時のみ)。リクエストヘッダは書かない。
- `--dry-run` の出力にもトークンは含めない(state.json と同じ形)。

## 5. 表示層 `plugin/fable.10s.py`

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
  ---
  プラン: max · 取得: 21:05:12 (3分前)
  エラー: token_expired (21:10:00)     ← エラー時のみ、赤
  ---
  今すぐ更新        | bash=<abs python3> param1=<abs fetch.py> param2=--force terminal=false refresh=true
  ログを開く        | bash=open param1=~/.cache/fable-meter/fetch.log terminal=false
  ```
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

uninstall.sh: bootout → plist 削除 → プラグイン削除 → キャッシュ削除は `--purge` 指定時のみ。

## 8. テスト(`python3 -m unittest`)

- `test_fetch.py`: fixture から `parse_usage()` が fable/seven_day/five_hour を正しく取り出す。`limits[]` から Fable を抜いた fixture → `fable_not_found`。`limits` 欠落 → `schema_error`。`percent` 非数値 → `schema_error`。state 書き出しがエラー時に前回の `data` を保持する。
- `test_plugin.py`: 鮮度 0/11/31 分で `F12% W9% S6%` / `F12%? …` / `F-- …` になる。`ok=false` で `!`。80%/95% 超で色パラメータが付く。state.json 欠落で `--`。日本語ラベル・`リセット` / `今すぐ更新` / `ログを開く` が出る。

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
| 9/1 プロモ終了 | 同じ作業量で % が跳ねる。異常ではない |
