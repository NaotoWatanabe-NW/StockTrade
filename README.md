# 株式監視・スクリーニングツール（SBI証券向け）

SBI証券には個人向けの正式な発注APIが提供されていないため、
**「分析・通知は自動化、発注は手動」**という安全な設計にしています。

**日本株・米国株の両方に対応。** 銘柄シグナル検出時には、ATR（平均値幅）を
基準にした**指値・損切り・利確の具体的な価格プラン**まで通知します。

```
yfinance（無料）でデータ取得（日本株 / 米国株）
        ↓
テクニカル指標を計算・シグナル判定
        ↓
指値・損切り・利確プランを算出（ATR基準）
        ↓
Discordに通知
        ↓
通知を見てSBI証券アプリで手動発注（あなたの判断）
```

---

## ディレクトリ構成

```
StockTrade/
├── config.py                    # 監視銘柄・閾値・注文プラン・サイジング等の設定＋実行時マージ用getter
├── main.py                      # メイン実行スクリプト（監視＋スクリーニング）
├── requirements.txt             # 依存パッケージ
├── stock.db                     # SQLite（保有・約定・シグナル・成績・価格キャッシュ・設定）
├── run_monitor.sh               # 監視の定期実行ランナー（cron用）
├── run_idle_check.sh            # 寝ている資産チェックの定期実行ランナー（cron用）
├── run_web.sh                   # Webアプリ（API＋フロント）を空きポートで同時起動
├── core/
│   ├── data_client.py           # yfinanceクライアント（日本/米国対応）
│   ├── market.py                # 市場判定（東証/米国・通貨・取引時間）
│   ├── indicators.py            # テクニカル指標・シグナル判定
│   ├── scoring.py               # 合議制スコアリング（複数指標の重み付き合算）
│   ├── strategy.py              # 1銘柄の総合評価（シグナル→方向→プラン）
│   ├── trade_plan.py            # 指値・損切り・利確の算出（ATR基準）
│   ├── orders.py                # SBI注文タイプ（指値/逆指値/OCO/IFD/IFDOCO）組立
│   ├── exit_rules.py            # 保有ロングの手仕舞いルール
│   ├── risk.py                  # ポジションサイジング（固定リスク%→株数）
│   ├── regime.py                # 相場レジーム判定（トレンド/チョップ）
│   └── events.py                # 決算回避フィルタ
├── screener/
│   ├── engine.py                # スクリーニング＋保有損益エンジン
│   ├── signal_log.py            # シグナル履歴の記録＋個別通知の紐付け
│   └── signal_outcome.py        # シグナルの予測 vs 実勢価格の決着評価
├── notifier/
│   ├── discord_notifier.py      # Discord通知（Webhook送信）
│   └── discord_bot.py           # Discord双方向Bot（リアクションで約定/見送り・任意）
├── data/
│   ├── db.py                    # SQLite接続・スキーマ・マイグレーション
│   ├── repository.py            # holdings/trades/signals/backtest/settings のCRUD
│   └── price_cache.py           # 価格データのDBキャッシュ
├── scripts/
│   ├── migrate_holdings.py      # 旧 holdings_local.py → DB 移行（初回のみ）
│   ├── migrate_watchlist.py     # 監視ユニバース → DB 移行（初回のみ）
│   ├── notify_idle_holdings.py  # 寝ている資産を検出してDiscord通知
│   └── evaluate_signal_outcomes.py # 過去シグナルの予測結果を評価して記録
├── backtest/
│   ├── runner.py                # バックテスト実行（CLI／Web共有の run_backtest）
│   ├── simulator.py             # 約定シミュレーション
│   ├── metrics.py               # 成績指標の算出
│   └── optimizer.py             # ウォークフォワード最適化
├── api/                         # FastAPIバックエンド（Webアプリ）
│   ├── main.py / deps.py / schemas.py
│   └── routers/                 # holdings/trades/pnl/portfolio/signals/watchlist/backtest/settings
├── web/                         # フロントエンド（Next.js + TypeScript）
│   ├── app/                     # 画面（ダッシュボード/保有/ウォッチリスト/取引/損益/シグナル/バックテスト/設定）
│   ├── components/              # UIコンポーネント
│   └── lib/                     # APIクライアント
└── docs/                        # 設計ドキュメント（戦略・Webアプリ・ロードマップ）
```

---

## セットアップ

### 1. 依存パッケージのインストール

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. Discord Webhookの設定と接続テスト

1. Discordチャンネル設定 → 連携サービス → ウェブフック → 新しいウェブフック → URLをコピー
2. `.env` を作成してURLを設定（`.env` は git管理外）:

   ```bash
   cp .env.example .env
   # .env を開いて DISCORD_WEBHOOK_URL=... を貼り付け
   ```

3. 疎通確認（Discordにテスト通知が届けばOK）:

   ```bash
   .venv/bin/python main.py --test-notify
   ```

> `export DISCORD_WEBHOOK_URL="..."` で環境変数に直接入れてもOK（`.env` より優先）。

### 3. 保有銘柄（口座情報）の登録

保有銘柄は SQLite（`stock.db`）で管理します。`config.py` の `get_holdings()` が
定期実行のたびに DB から最新を読むため、**Webアプリで編集した内容が監視ツールに即反映**されます。

- **登録・編集**: 後述の「取引記録 Web アプリ」の保有銘柄画面から操作（推奨）
- **旧形式からの移行**: かつて `holdings_local.py` に書いていた場合は、1回だけ実行して取り込めます

  ```bash
  .venv/bin/python -m scripts.migrate_holdings
  ```

各銘柄が持つ項目:

- `avg_price`（建値）→ 含み損益率を計算
- `shares`（保有株数）→ 含み損益を**金額でも**表示
- `long_term`（長期保有フラグ）→ 寝ている資産の売却候補抽出（後述）から除外

### 4. スクリーニング対象の調整（任意）

`config.py` の `SCREENING_UNIVERSE_JP` / `SCREENING_UNIVERSE_US` を編集します。

---

## 使い方

### 1回だけ実行

```bash
python main.py
```

### 定期実行（市場時間中、自動で繰り返し）

```bash
python main.py --loop --interval 60   # 60分間隔
```

cron で回す場合は同梱の `run_monitor.sh` を使う:

```cron
# 平日 9:30 と 15:30 に1回ずつ実行
30 9,15 * * 1-5 /path/to/StockTrade/run_monitor.sh >> /path/to/StockTrade/cron.log 2>&1
```

### 寝ている資産（塩漬け）の抽出・通知

スイングでは「1日の値動き（ATR）が小さい銘柄」は値幅を取れず資金が寝る。
`notify_idle_holdings` は、**長期保有フラグ(`long_term`)を除いた保有**のうち
ATR%/日が閾値未満の銘柄を抽出し、戻り売り指値・撤退逆指値を付けて Discord に通知する。

```bash
.venv/bin/python -m scripts.notify_idle_holdings              # 通知を送信
.venv/bin/python -m scripts.notify_idle_holdings --dry-run    # 送信せず内容を表示
.venv/bin/python -m scripts.notify_idle_holdings --atr-max 1.5  # 閾値（%/日）を変更
```

| オプション | 意味 | 既定 |
| --- | --- | --- |
| `--atr-max` | 寝ている判定とする ATR%/日 の上限 | `2.0` |
| `--include-long-term` | 長期保有フラグの銘柄も対象に含める | 除外 |
| `--dry-run` | Discordに送らずコンソール表示のみ | 送信する |

cron で回す場合は同梱の `run_idle_check.sh` を使う（引数はそのまま透過）:

```cron
# 毎週月曜 8:00 に1回（場中前に売却候補を確認）
0 8 * * 1 /path/to/StockTrade/run_idle_check.sh >> /path/to/StockTrade/cron.log 2>&1
```

---

## Web アプリ

保有・ウォッチリストの管理、約定履歴・実現損益、シグナル追跡、バックテスト、
パラメータ設定までをブラウザから操作できます。データは SQLite（`stock.db`）で
監視ツールと共有し、保有・ウォッチリスト・パラメータの編集は**次回スキャンに即反映**されます。

### 起動（推奨：API＋フロントを同時起動）

```bash
./run_web.sh          # 開発モード（uvicorn --reload ＋ npm run dev）
./run_web.sh --prod   # 本番モード（next build → start）
```

`run_web.sh` は**空きポートを2つ自動取得**し、`web/.env.local` の
`NEXT_PUBLIC_API_BASE` をAPIのポートに自動で書き換えて両者を起動します
（Ctrl-C で両方停止）。起動ログに URL が出ます。

> ⚠️ 個別に起動する場合は、フロントの `web/.env.local` の `NEXT_PUBLIC_API_BASE` を
> バックエンドの URL に合わせること（ポート不一致だと接続エラーになります）。

単体起動の例:

```bash
.venv/bin/uvicorn api.main:app --reload --port 8000   # /docs で Swagger UI
cd web && cp .env.local.example .env.local && npm run dev
```

### 初回のみ：既存の保有を DB へ移行

```bash
.venv/bin/python -m scripts.migrate_holdings
```

### 画面一覧

| 画面 | 内容 |
| --- | --- |
| ダッシュボード | 保有評価・ポートフォリオ熱量などの概況 |
| 保有銘柄 | 建値・株数・長期保有フラグの管理（監視ツールに即反映） |
| ウォッチリスト | スクリーニング対象ユニバースの管理 |
| 取引記録 | 約定の記録。シグナル紐付けの有無（予測ベース/単独注文）を表示 |
| 損益 | 実現損益（譲渡益課税を反映した税引後も表示） |
| シグナル | シグナル追跡（約定/決済の記録・ライブ成績 vs バックテスト・スコア較正・推奨サイジング） |
| バックテスト | パラメータを指定してWeb実行・履歴・資産曲線 |
| 設定 | 売買シグナル・資金管理のパラメータ編集（保存で即反映） |

---

## 検出するシグナル

| シグナル | 条件 |
|---------|------|
| 🟢 ゴールデンクロス | 短期MAが長期MAを上抜け |
| 🔴 デッドクロス | 短期MAが長期MAを下抜け |
| 🟢 RSI売られすぎから回復 | RSIが30を下から上に通過 |
| 🔴 RSI買われすぎから反落 | RSIが70を上から下に通過 |
| 📊 出来高急増 | 出来高が20日平均の2倍以上 |
| 🚀 高値ブレイクアウト | 過去30日高値を更新 |
| 📉 安値ブレイクダウン | 過去30日安値を割り込み |

閾値は `config.py` の `SCREENING_CONFIG` または **Webの「設定」画面**から調整できます
（設定画面の変更は次回スキャン／バックテストに即反映）。

---

## 注文プラン（SBI証券の注文タイプで指示）

シグナル検出時、ATR（直近14日の平均値幅）を基準に価格を算出し、
**SBI証券の注文タイプ（指値・逆指値・OCO・IFD・IFDOCO）に変換して指示**します。

### 価格の決め方

| 用途 | 価格 |
| --- | --- |
| 押し目買いの指値 | 現値 − 0.5×ATR |
| ブレイク買いの逆指値 | 現値 + 0.5×ATR（飛び乗り） |
| 損切り | エントリー − 2.5×ATR |
| 利確 | エントリー + リスク幅×3（RR 3:1） |
| 戻り売りの指値（手仕舞い） | 現値 + 0.5×ATR |
| 撤退の逆指値（手仕舞い） | 現値 − 2×ATR |

倍率は `config.py` の `TRADE_PLAN_CONFIG`（または Webの「設定」画面）で調整できます。
上表は既定値で、出口・サイジングは `EXIT_CONFIG` / `RISK_CONFIG` でさらに細かく調整します。

### 注文タイプの自動選択

- **新規買い・買い増し** → 既定で **IFDOCO**
  （1次で買い、約定後にOCO＝利確の売り指値＋損切りの売り逆指値が自動で有効化）
  - 押し目シグナル（ゴールデンクロス/RSI反発）は**買い指値**、
    高値ブレイクは飛び乗りの**買い逆指値**を自動で使い分け
- **保有ロングの手仕舞い**（売りシグナル） → 既定で **OCO**
  （戻り売りの指値＝利確 ＋ 撤退の逆指値＝損切り）

`config.py` の `ORDER_CONFIG` で注文タイプを切り替えられます:

```python
ORDER_CONFIG = {
    "entry_order_type": "IFDOCO",  # "IFDOCO" / "IFD" / "SIMPLE"(指値・逆指値のみ)
    "exit_order_type":  "OCO",     # "OCO" / "STOP"(逆指値のみ)
}
```

通知例（保有銘柄が売りシグナル時）:

```text
🧾 推奨注文：OCO
利確/戻り売り: 売り指値 ¥1,010
損切り/撤退: 売り逆指値 ¥960
```

> ⚠️ 価格・注文タイプはあくまで目安です。最終判断・発注はSBI証券で手動で行ってください。
> 空売り（信用新規売り）は対象外で、売りシグナルは「保有していれば手仕舞い」の提案です。

---

## 合議制スコアリング（確度の指標）

単発シグナルのダマシを減らすため、複数指標に −1〜+1 を付けて重み付き合算し、
**総合スコア −100〜+100**（強い買い／買い／中立／売り／強い売り）を出します。

| コンポーネント | 見るもの | 既定の重み |
| --- | --- | --- |
| trend | 移動平均の並び・価格との位置・長期MAの傾き | 0.30 |
| macd | MACDとシグナルの位置＋ヒストグラムの拡大 | 0.20 |
| breakout | 高安レンジ内での位置（上限突破=強気） | 0.20 |
| rsi | 売られすぎ/買われすぎ（50中心の逆張り） | 0.15 |
| volume | 当日の値動きを出来高が裏付けているか | 0.15 |

- スクリーニング結果は**スコア降順**で並び、通知に内訳（寄与上位）も表示
- 重み・閾値は `config.py` の `SCORING_CONFIG` で調整して精度を実験できます
- `min_abs_score` を上げると確度の高い候補だけに絞り込めます（0=無効）

---

## バックテストと戦略検証

ライブ通知とまったく同じ意思決定ロジックを過去データに適用し、勝率・期待値R・
プロフィットファクター・最大DD・約定率・Sharpe・年率リターンを算出します。
履歴は `stock.db` の `price_history` にキャッシュ（~5年）し、レート制限を回避します。

```bash
# CLI（結果を DB に保存）
.venv/bin/python -m backtest.runner --universe JP --save
.venv/bin/python -m backtest.runner --no-regime --save     # レジームフィルタ無効で比較
.venv/bin/python -m backtest.runner --optimize             # ウォークフォワード最適化
```

Webの「バックテスト」画面からは、**ユニバース・各パラメータを指定して実行**できます
（バックグラウンド実行＋ポーリングで状態表示。結果と資産曲線・使用パラメータを履歴で確認）。

---

## シグナル追跡とライブ約定率

スクリーナーが出したシグナルは `signals` テーブルに記録され、Webの「シグナル」画面で
追跡できます。実際にエントリー／決済したら**約定を記録**すると（`signal_id` 付きの取引として
取引記録・損益にも反映）、状態が `OPEN → 建玉中 → 決済済` と進み、**実現R**が自動計算されます。

- **ライブ成績 vs バックテスト期待値**: 実取引の勝率・平均Rを最新バックテストの期待値と比較
- **スコア較正**: スコア帯ごとの予測的中度（`python -m scripts.evaluate_signal_outcomes` で更新）
- **ライブ約定率**: 有効期限（既定15営業日）を過ぎた OPEN は読み取り時に自動で `期限切れ` へ遷移し、
  約定到達率が滞留せず正確に出ます

---

## パラメータ設定（Webで編集・即反映）

売買シグナル検出・出口・資金管理のしきい値を、Webの「設定」画面で編集できます。
保存値は `stock.db` の `settings` に永続化され、`config.py` の getter が**実行時にマージ**するため、
**次回のスキャン／バックテストに再起動なしで反映**されます。編集対象はスクリーニング・スコア・
注文プラン・出口・レジーム・資金管理（口座サイズ等）のパラメータです。

---

## 推奨サイジング（口座残高 → 何株買うか）

固定リスク%方式（`許容リスク額 = 口座サイズ × リスク%`、損切り幅で割って株数を算出）で、
OPEN な買いシグナルごとの**推奨株数・投資額・実リスク額**を提示します。口座サイズ・1トレード
リスク%・同時保有上限は `RISK_CONFIG`（設定画面で編集可）に従います。Webの「シグナル」画面の
「推奨サイジング」カードで確認できます。

---

## 実現損益と譲渡益課税

「損益」画面は実現損益を表示し、上場株式等の**譲渡益課税（申告分離 20.315%）**を反映した
税引後も表示します。利益（譲渡益）にのみ課税し、同一通貨グループ内で損益通算してから
課税対象を算出します（`config.py` の `TAX_CONFIG`）。

---

## Discord 双方向 Bot（任意）

Webhook は送信専用のため、**スタンプ（リアクション）で操作を反映する**には Bot を使います。
有効化すると新規シグナルを1件ずつ個別通知してメッセージIDを保存し、その通知への
リアクションでシグナル状態を更新できます（✅=約定を記録 / ❌=見送り）。

```bash
# 任意依存（未インストールでも本体は動く）
.venv/bin/pip install discord.py

# 個別通知を有効化（config.py）
#   NOTIFY_CONFIG["per_signal_tracking"] = True

# Bot を起動（DISCORD_BOT_TOKEN と API_BASE を .env / 環境変数に設定）
.venv/bin/python -m notifier.discord_bot
```

---

## ⚠️ 重要な注意事項

- **このツールは発注を行いません**。シグナルはあくまで参考情報です
- SBI証券のAPIは個人向けに提供されていないため、最終的な売買判断・実行は必ず手動で行ってください
- yfinanceのデータは15〜20分遅延します。デイトレ用途には不向きです（スイング向け設計）
- 連続リクエストでYahoo!ファイナンス側にブロックされる可能性があるため、`--interval` は60分以上を推奨

---

## 銘柄ユニバースの拡張

現在は手動リストですが、東証上場銘柄全体から動的にスクリーニングしたい場合は、
**J-Quants API**（日本取引所グループ公式・無料）と組み合わせることで全銘柄対応が可能です。
興味があれば追加実装します。

---

## 今後の拡張案

実装済み:

- [x] バックテスト機能（過去のシグナルが実際どうだったか検証）＋ Web実行
- [x] Webでシグナル履歴・ライブ成績・スコア較正を可視化
- [x] パラメータの Web 編集（実行時マージ）
- [x] 口座残高からの推奨株数サイジング
- [x] ライブ約定率の自動記録（期限切れ自動遷移）
- [x] Discord 双方向 Bot（リアクションで約定/見送り・雛形）

検討中:

- [ ] 機械学習による売買パラメータ（しきい値）の最適化 ※シグナル自体はテクニカルのまま（`docs/ROADMAP_PLAN.md` に構想）
- [ ] J-Quants APIで東証全銘柄を動的取得
- [ ] 配当利回り・株主優待情報を加味したスクリーニング条件
