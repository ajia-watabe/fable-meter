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
  SwiftBar v2.1.1 の `buildMenuItem()` / `patchMenuItem()` は
  `let needsAction = params.hasAction || params.color != nil` で
  `#selector(perfomMenutItemAction)` を割り当てる
  (`SwiftBar/MenuBar/MenuBarItem.swift:1518`、`:973`。タグ `v2.1.1` = build 597 = 現在の
  最新リリースであり、未リリースの main も同じロジック)ので、`color=` を付けただけの行も
  クリック可能な項目になる(SwiftBar に `disabled=` 相当のパラメータは無い)。
- 色は **`ansi=true` + ANSI エスケープ**で付ける。`ansi` は `hasAction` にも `color` にも
  影響しないので、**色付き かつ アクション無し(=クリック不可)** が両立する。
  検証: メニューを開いた状態で Accessibility から読むと、状態表示行は全て
  `enabled=false`、操作行 3 つだけが `enabled=true`。

### macOS 26 のハイライトについて(既知の制約)

macOS 26(Tahoe、実測 26.3 / build 25D125)では、**無効な `NSMenuItem` でもポインタを
載せると選択ハイライト(紫)が描かれる**。`color=` の有無・`ansi` の有無とは無関係で、
色を一切付けていない `予測:` / `取得:` 行でも同じように出ることを全行で実測した。
クリックは無効のまま効かないが、ハイライトの抑止は
- プラグイン側の出力(SwiftBar には `disabled=` が無い)
- SwiftBar のバージョン(最新リリース 2.1.1 も main も判定は同じ)

のどちらでも不可能。将来 SwiftBar 側で fold 行のようなカスタム `NSView` 描画
(`FoldableMenuItemView`)が一般の行にも使えるようになれば回避できる可能性がある。
  `atributedTitle()` は `params.ansi` のとき `params.color` の上書きをせず
  (`MenuBarItem.swift:1556-1566`)、`font`/`size` は ANSI の後に全域へ適用されるので
  `font=Menlo size=12` と併用できる。
- 使える色の制約(いずれも実測 + SwiftBar のソースで確認):
  - 無効(`action == nil`)な `NSMenuItem` でも AppKit は**有彩色**の `attributedTitle` を
    そのまま描く。ただし r==g==b の**無彩色**は無効項目用の淡色に差し替えられる
    (実測: `(188,188,188)` は淡色化、`(188,188,190)` はそのまま)。
    → ANSI 256 色のグレースケール段(232-255、`NSColor.colorForAnsi256ColorIndex`)は使えない。
  - ANSI 24bit(`38;2;r;g;b`)は SwiftBar が未対応(`attributesForANSICodes` は `38;5;N` だけ)。
  - 16 色は `NSColor.systemRed` などの動的カラーに落ちる(`String+ANSIColor.swift:3-21`)ので
    ライト/ダーク自動。ただし `39` は「前景色を消す」に予約されていて `labelColor` は取れない。
  - 256 色(16-231)の RGB 変換式は上流にバグがあり、実質「青寄りの派手な色」しか出ない。
    その中で無彩色に近いのは 189 = `(247,248,255)`(ほぼ白)。
- 実際の割り当て: 3 枠の行 = ダークのみ `\e[38;5;189m`(本文色相当)、ライトは無色
  (ANSI に「黒に近い有彩色」が無いため)。`予測:` / `取得:` 行 = 無色(副次情報)。
  エラー行 = `\e[31m`(`systemRed`)。赤が出るので ⚠️ プレフィクスは廃止。
- ライト/ダークの判定は SwiftBar が渡す `OS_APPEARANCE`(`Plugin.swift:296`)。
  テーマ切替時にプラグインは再実行されず既存出力を再描画するだけ
  (`PluginManger.swift:352`)なので、切替直後は最大10秒だけ前のテーマの色が残る。
- エスケープはリテラルの `\e` ではなく **実際の ESC バイト(0x1b)** を出す。
  `atributedTitle()` は ANSI 変換の前に `unescape()`(`MenuBarItem.swift:1491`)を通し、
  `\e` の backslash を落として `e[38;5;189m` にしてしまうため。
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
  予測: リセット時点 ~34%              ← 履歴が足りなければ「データ収集中(あと約2時間)」「…(あと約30分)」
  ---
  取得: 21:05:12 (3分前)
  エラー: token_expired (21:10:00)     ← エラー時のみ(ANSI 赤)
  ---
  リフレッシュ      | bash=<abs python3> param1=<abs fetch.py> param2=--force terminal=false refresh=true sfimage=arrow.clockwise
  使用量ページを開く | href=https://claude.ai/settings/usage sfimage=safari
  ログを開く        | bash=open param1=~/.cache/fable-meter/fetch.log terminal=false sfimage=doc.text
  ```

### ペース予測(`予測:` 行)

予測は **線形 ← A ← A+B** のフォールバック梯子で構成する。データが足りない側に自動的に落ちる。
**捏造はしない**: 足りなければ落とすか、行を「データ収集中」にする。

#### 1. 窓の切り出し(共通)

`history.jsonl` を読み、**現在の窓の点だけ**を取る。新しい順に遡り、`fable_resets_at` が
現在の `resets_at` と違う点、または(時系列で見て)% が下がる直前の点で打ち切る。
**`resets_at` はフェッチごとに秒未満が揺れる**(実測: `...15:00:00.051929` → `...15:00:00.487294`)ため、
文字列一致ではなく**分単位に丸めて**比較する。この揺れは**分の境界をまたぐ**(実測: 同じ枠が
`...15:00:00.051929` と `...14:59:59.579336` の両方で返る)ので、**切り捨てではなく四捨五入**
(`int((timestamp + 30) // 60)`)で丸める。切り捨てだと 15:00 と 14:59 で別キーになり、
ほぼ毎回窓が 1 点に切り詰められて収集中カウントダウンが進まなくなる(実測)。
揺れは 1 秒未満なので前後 30 秒の許容で十分。同じ理由で §4.2 のリセット判定も 60 秒以下の前進は無視する。
% の低下はリセットが起きた証拠なので、それより前は捨てる。

**ゲート**: 点が 2 個未満、最古と最新の間隔が 3 時間未満、`resets_at` が不明/過去 → 外挿しない(6 へ)。

#### 2. 成分A: 直近重み付き傾き(`pace_slope` / `weighted_slope`)

端点 2 点の傾き(最古と最新だけ)は、窓の最初の点に永久に引きずられる。代わりに窓の
**全点**を重み付き最小二乗で当てる。重みは古さの指数減衰:

```
w_i = 0.5 ** (age_hours_i / SLOPE_HALF_LIFE_HOURS)   # 半減期 12 時間
slope = (Σw·Σwxy - Σwx·Σwy) / (Σw·Σwxx - (Σwx)^2)
```

- `age_hours_i` は**最新点からの経過時間**(壁時計)。12 時間前の点は重み 1/2、24 時間前は 1/4。
- 分母が 0 近傍(全点が同時刻、重みが 1 点に潰れた等) → **0.0 を返す**(例外にしない)。
- 傾きは **0 で下限クランプ**する。窓の中で使用量が減ることはない(減っていたらリセット済みで、
  それは 1 で切られている)。
- 点が 2 個ちょうどなら重み付き最小二乗は 2 点をちょうど通るので、**従来の端点傾きと一致する**。
  つまり成分Aは既存挙動の一般化であり、点が増えたときだけ挙動が変わる。

#### 3. 成分B: 1日の活動カーブ(`activity_curve` / `effective_hours`)

「残り 137 時間」のうち、実際に使うのはその一部の時間帯だけ。壁時計をそのまま掛けると
夜通し使い続ける前提の予測になる。そこで **hour-of-day 24 バケットの活動プロファイル**を作る。

- `history.jsonl`(8 日保持)の**連続する 2 サンプル**を見る。**同じ窓**(`reset_key` 一致)で
  `delta% > 0` のペアだけを採用し、`delta%` を**またぐ時刻バケットへ時間比で按分**して足す
  (例: 0:30→2:00 の +3% は、0 時台に 1%、1 時台に 2%)。バケットは**ローカル時刻**
  (人間の生活リズムなので UTC ではない)。
- 全日分を合計したのち `share[h] = total[h] / Σtotal`(合計 1)に正規化する。
- **ゲート**: 履歴全体の観測幅が **24 時間以上**、かつ合計 `delta% > 0`。満たさなければ
  `None` = 成分B は無効(A のみで壁時計に戻る)。
- 夜のバケットが 0 なのは**欠測ではなく情報**。share 0 の時間帯は実効時間に一切寄与しない。

**実効時間**: 区間 [start, end) をローカル時刻境界で刻み、各断片の `share[h] × 断片の時間` を
足して、平均 share = 1/24 で割る:

```
effective_hours = (Σ share[h] × 時間) / (1/24) = (Σ share[h] × 時間) × 24
```

- 完全に平坦なカーブ(全 share = 1/24)なら **実効時間 = 壁時計時間**(退化して A と一致)。
- 12 時間だけ活動するカーブなら、活動中は 1 時間が実効 2 時間、非活動中は実効 0 時間。
- ちょうど 24 時間の区間は、カーブの形によらず必ず実効 24 時間になる(share の総和が 1 のため)。

#### 4. 予測(`project`)

成分B が有効なとき、**傾きの当てはめも残り時間も実効時間で測る**。単位を揃えないと
「壁時計での %/h」に「実効時間」を掛けることになり意味を成さない。具体的には、
各サンプルの時刻を「窓の最初の点からの実効経過時間」に変換してから 2 の当てはめを行う
(`pace_slope(points, shares)` の `xs`)。

```
slope     = 成分A(実効時間軸、成分B が無ければ壁時計軸)
remaining = effective_hours(shares, 最新点t, resets_at)   # 成分B 無し = 壁時計の残り時間
projected = 最新% + slope × remaining
```

`projected` は `[最新%, 200]` にクランプする。

- `projected <= 100` → `予測: リセット時点 ~34%`。
- `projected > 100`(かつ最新 % が 100 未満)→ **カーブを前方に歩いて** 100% 到達時刻を出す。
  必要実効時間 `(100 - 最新%) / slope` を、時刻境界ごとに `share[h] × 24` の速度で積み上げ、
  到達した**壁時計時刻**を `予測: 100%到達 9/3 15時ごろ` として表示する。
  成分B が無ければ従来どおり `最新t + (100 - 最新%) / slope`。
  上限ステップ(`CURVE_MAX_STEPS` = 40 日相当)内に到達しなければ到達時刻は出さず値だけ出す。
- 最新 % が既に 100 以上なら到達時刻は出さず値だけ出す。
- **残り時間の share が全て 0**(例: 一度も活動していない時間帯にリセットが来る)なら
  実効残りは 0 で `projected = 最新%`。これは仕様どおり(伸びないと予測する)。

#### 5. フォールバック梯子

| 条件 | 使うもの |
|---|---|
| 窓に 2 点以上・間隔 3 時間以上・履歴 24 時間以上・消費 > 0 | **A + B**(実効時間) |
| 上のうち履歴が 24 時間未満 / 消費が 0 | **A のみ**(壁時計) |
| 窓が 2 点未満 / 間隔 3 時間未満 | 予測せず「データ収集中」行(6) |
| `ok != true` / 鮮度 10 分以上 / `resets_at` 不明・過去 | **行自体を出さない** |

古い state や失敗中の state に基づく予測はしない。**表示は成分の別を出し分けない**
(サフィックス等は付けない)。UI を汚さずに精度だけが上がる設計で、
「数日使うと予測が安定する」ことは README で説明する。

#### 6. データ収集中の行

状態が新しく `ok` なのに 1 のゲートで外挿できないときは、行を消さずに
`予測: データ収集中(あと約N時間)` / `予測: データ収集中(あと約N分)` を出す。
残り `= 3時間 - (now - 今の枠の最古の点)`(点が1つも無ければ 3 時間)として、
残りが **90 分以上なら時間表示**(`N = ceil(残り / 1時間)`)、**90 分未満なら分表示**
(`N = 10分単位に切り上げ`、下限 10 分)。時間表示だけだと終盤に「約1時間」で 1 時間
止まって見えるため。「何も出ない」と「まだ足りない」を区別するため行自体は消さない。

#### 7. その他

- 色は付けない(下記の理由)。
- すべて純関数で、`now` は注入できる。表示層はネットワークにも Keychain にもアクセスしない。

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
- `test_plugin.py`(適応予測): 成分Aの重み付き傾き(加速する系列で直近が効く・端点が同じでも
  消費が直近に寄っている方が速い・2点なら端点傾きと一致・傾きの 0 クランプ・同時刻でも落ちない)、
  成分Bのカーブ生成(時刻境界をまたぐ按分・正規化・2日分の加算・窓またぎのペアを無視・
  24 時間未満や消費 0 ではカーブ無し)、実効時間(平坦カーブ = 壁時計・活動 12 時間なら 2 倍速・
  非活動帯は 0・24 時間区間は常に実効 24 時間)、A+B の予測(夜に寄った残りは縮む・
  平坦カーブは A と一致・残りが全て非活動なら伸びない・カーブを歩いた 100% 到達時刻)、
  梯子のゲート(履歴 24 時間未満は A のみ・3 時間未満は収集中)。
  すべてローカル時刻に依存しないよう、期待値は基準時刻のローカル時から組み立てる。

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
