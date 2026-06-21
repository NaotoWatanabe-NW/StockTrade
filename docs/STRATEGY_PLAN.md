# スイング取引アルゴリズム強化 設計プラン

> 本書は「勝率・利益率を高める」ための強化ロードマップ（設計）。**実装は別途指示で行う。**
> 前提：SBI証券の注文方法を活用し、エントリーは **15営業日以内** の約定を狙うスイング。

---

## 0. 確定した前提パラメータ

| 項目 | 確定値 | 備考 |
|------|--------|------|
| 15営業日の意味 | **エントリー注文の有効期限**（GTC的な期間指定の上限） | 約定後の保有期間の上限ではない。保有中の手仕舞いは別パラメータ（§5 `EXIT_CONFIG.time_stop_days`）として独立にチューニングする |
| 口座サイズ（推奨） | **¥1,000,000** | サイジングの基準。実値はユーザが調整 |
| 1トレード許容リスク（推奨） | **口座の 1.0%** | 1トレードで失ってよい最大額 = 口座 × 1% |
| 同時保有上限（推奨） | **5銘柄** | ポートフォリオ全体リスク（ヒート）を ~5% に制限 |
| バックテスト履歴 | **約5年・日足**、**ローカルDBにキャッシュ** | yfinanceのレート制限回避。`stock.db` に履歴テーブルを追加 |

> **運用上の注意（SBIの実態）**：保有後の決済注文（OCO等）にも期間指定の上限（≤15営業日）があるため、保有が15営業日を超える場合は決済注文の置き直しが必要。`time_stop_days` を 15以内に設定すれば置き直し不要で運用がシンプルになる。

---

## 1. 設計の核：「単一の意思決定関数」

最大の設計判断は、**バックテストとライブ通知がまったく同じロジックを通ること**。
現状は判定が [`screener/engine.py`](../screener/engine.py) に埋まっているため、純粋関数として切り出し、過去バーにも最新バーにも同一に適用する。これで「バックテストで良かった戦略」と「実際に通知される戦略」が乖離しない。

```
strategy.evaluate(df[:t], ctx, cfg) -> Decision
    Decision = { signals, consensus, filters, trade_plan, sizing, order }
```

- **ライブ**：`t = 最新バー` で呼ぶ（現 engine の役割）
- **バックテスト**：`t = 各過去バー` でループして呼ぶ

これにより A〜F の全強化が「同じ意思決定関数を通る」ことが保証される。

---

## 2. ターゲット・モジュール構成

既存は壊さず追加する（★＝新規）。

```
core/
  indicators.py      既存：指標 + detect_signals（ADX等を追加）
  scoring.py         既存：合議スコア（重み・閾値は F の最適化対象）
  trade_plan.py      既存：エントリー/損切り/利確の価格
  orders.py          既存：SBI注文タイプ変換
  market.py          既存
  regime.py      ★ B：上位足(週足)トレンド ＋ 指数レジーム判定
  events.py      ★ B：決算日取得・15日window内かの判定
  exit_rules.py  ★ D：出口状態機械（タイムストップ/部分利確/建値/トレーリング）
  risk.py        ★ E：固定リスク% → 株数サイジング
  strategy.py    ★  ：上記を束ねる単一意思決定関数 evaluate()
screener/
  engine.py          既存：strategy.evaluate() を最新バーに適用（薄くなる）
data/
  db.py              既存：price_history テーブルを追加（§6）
  price_cache.py ★ A：履歴のDBキャッシュ取得（無ければyfinance→保存）
backtest/
  simulator.py   ★ A：1銘柄をバー単位で走らせトレード再現（15日window）
  metrics.py     ★ A：勝率/期待値R/PF/最大DD/約定率/平均保有日数
  runner.py      ★ A：ユニバース集計・レポート出力（CLI）
  optimizer.py   ★ F：ウォークフォワードでパラメータ探索
```

---

## 3. データフロー（強化後）

```
履歴取得（~5年, 日足+週足）── data/price_cache（DBキャッシュ→無ければyfinance）
        │
        ▼
add_technical_indicators（+ ADX 等）
        │
        ▼
strategy.evaluate(df[:t]) ──────────────────────┐
   1) regime: 週足トレンド / 指数レジーム (B)    │ ← 通らなければ「見送り」
   2) detect_signals + compute_consensus         │
   3) events: 15日window内に決算なら除外 (B)     │
   4) trade_plan: エントリー価格（C: 押し目深さ）│
   5) risk: 株数サイジング (E)                    │
   6) orders: SBI注文へ変換                       │
        │                                         │
   ┌────┴───────────────┐                         │
   ▼                    ▼                         │
ライブ:Discord通知   backtest.simulator ◄─────────┘
                      出口は exit_rules (D)
                      → metrics 集計 (A)
                      → optimizer で再チューニング (F)
```

---

## 4. 中心モデル：「トレード・ライフサイクル」

`exit_rules.py` と `backtest/simulator.py` が共有する有限期間モデル。SBIの期間指定（エントリー有効期限15営業日）と直結する設計の背骨。

```
[signal@t] フィルタ通過・合議スコアOK
   → エントリー注文（指値/逆指値）発注。★有効期限 = 15営業日★
        ├ 15営業日内に高値/安値がエントリー価格にタッチしない
        │     → 「不約定(no-fill)」として記録（負けではなく機会損失）
        └ タッチ → 約定。建玉オープン
   → 建玉管理（各バー）:
        ├ 損切りタッチ            → 損切り（負け）
        ├ 第1利確(例:1R)タッチ    → 半分利確 ＋ 残りのストップを建値へ
        ├ トレーリング更新（ATR）
        └ time_stop_days 経過     → 引けで手仕舞い（保有中の時間切れ）
   → Trade{ entry, exit, R, pnl, bars_held, exit_reason } を記録
```

**ルックアヘッド回避（厳守）**
- 指標・シグナルは `df[:t]`（バーt以前）のみで計算。
- 約定判定は **翌バー以降の高安タッチ**で行い、シグナル足の終値で即約定としない。
- 約定価格は指値=指値価格、逆指値=トリガ価格（必要ならギャップ・スリッページを上乗せ）。

---

## 5. 強化レバー A〜F とマッピング

| レバー | 内容 | 主担当モジュール | 目的 |
|--------|------|------------------|------|
| **A 評価基盤** | バックテスト＋メトリクス | `backtest/`, `data/price_cache.py` | 全強化の効果を数値で判定する土台 |
| **B エントリー精度** | 週足トレンド/指数レジーム/ADX/決算回避/スコア閾値の実効化 | `core/regime.py`, `core/events.py`, `core/indicators.py`(ADX), `config`(`min_abs_score`) | 勝率↑（ダマシ除外） |
| **C エントリー執行** | 15日内に約定する押し目深さ（ATR係数）最適化、確認足 | `core/trade_plan.py`, `config` | 約定率↑ |
| **D 出口・リスク管理** | タイムストップ/部分利確/建値ストップ/ATRトレーリング/動的RR | `core/exit_rules.py` | 利益率↑・勝率↑（インパクト大） |
| **E ポジションサイジング** | 固定リスク%→株数。同時保有上限 | `core/risk.py`, `config` | 口座利益率↑・DD制御 |
| **F 最適化** | 重み・閾値・ATR係数をウォークフォワード探索 | `backtest/optimizer.py` | 過最適化を避けつつ最終調整 |

### config 追加スケッチ

```python
# 出口・リスク管理（D）
EXIT_CONFIG = {
    "time_stop_days":    15,    # 保有中の時間切れ手仕舞い（エントリー有効期限とは独立）
    "partial_tp_r":      1.0,   # 第1利確を何R地点に置くか
    "partial_tp_pct":    0.5,   # 第1利確で利確する割合
    "move_to_breakeven": True,  # 部分利確後に残りのストップを建値へ
    "trail_atr_mult":    2.0,   # ATRトレーリングの幅
}

# サイジング（E）— 推奨値
RISK_CONFIG = {
    "account_size":       1_000_000,  # 口座サイズ（推奨。ユーザ調整）
    "risk_per_trade_pct": 1.0,        # 1トレード許容リスク（口座の%）
    "max_positions":      5,          # 同時保有上限
}

# レジーム/上位足フィルタ（B）
REGIME_CONFIG = {
    "jp_index": "^N225", "us_index": "^GSPC", "index_ma": 50,
    "weekly_trend_filter": True, "adx_min": 20,
}

# イベント回避（B）
EVENTS_CONFIG = {"avoid_earnings_within_days": 15}

# バックテスト（A）
BACKTEST_CONFIG = {
    "history": "5y", "fee_pct": 0.0, "slippage_atr": 0.1,
    "entry_order_valid_days": 15,   # 確定前提：エントリー注文の有効期限
}
```

> `SCORING_CONFIG` の `weights / thresholds / min_abs_score`（[`config.py`](../config.py)）は F の最適化対象変数。現状 `min_abs_score=0` でスコア確度フィルタが実質無効なので、A基盤で最適値を求めて実効化する。

---

## 6. 履歴キャッシュ（ローカルDB）

既存 [`data/db.py`](../data/db.py) のスキーマ流儀（`CREATE TABLE IF NOT EXISTS`、`init_schema` で保証）に合わせ、`stock.db` に履歴テーブルを追加する。

```sql
CREATE TABLE IF NOT EXISTS price_history (
    code      TEXT NOT NULL,            -- 証券コード/ティッカー
    interval  TEXT NOT NULL,            -- "1d" / "1wk"
    date      TEXT NOT NULL,            -- YYYY-MM-DD
    open      REAL, high REAL, low REAL, close REAL,
    volume    REAL,
    PRIMARY KEY (code, interval, date)
);
CREATE INDEX IF NOT EXISTS idx_price_code ON price_history(code, interval, date);
```

- `data/price_cache.py`：`get_history_cached(code, interval, years=5)` を提供。DBに無い期間だけ yfinance から取得して **upsert**、以後はDBから読む。
- バックテスト・ライブ双方がこのキャッシュ経由にすれば、レート制限を避けつつ再現性も担保できる。
- 既存 [`core/data_client.py`](../core/data_client.py) は薄いラッパとして残し、内部でキャッシュを参照する形に寄せる。

---

## 7. 評価メトリクス（「勝率・利益率」の定義）

| 指標 | 意味 | 目標方向 |
|------|------|----------|
| 勝率 | 勝ちトレード / 決済トレード | ↑ |
| 期待値(R) | 1トレード平均損益（リスク単位） | ↑（> 0 必須） |
| プロフィットファクター | 総利益 / 総損失 | ↑（> 1.5 目安） |
| 最大ドローダウン | 資産曲線の最大下落 | ↓ |
| **約定率** | シグナルのうち15営業日内に約定した割合 | 制約由来の新軸 |
| 平均保有日数 / タイムストップ比率 | 出口の質 | 監視 |
| 期間リターン（サイジング適用後） | 口座ベースの増減 | ↑ |

---

## 8. 段階計画（各フェーズ後に必ず数値で再測定）

| Phase | 内容 | 成果物 | 完了条件 |
|-------|------|--------|----------|
| **0** | 意思決定パスを `strategy.evaluate()` に集約（挙動不変リファクタ） | `core/strategy.py`、engine薄化、ゴールデンテスト | 既存テスト全緑・出力不変 |
| **1** | 履歴キャッシュ＋バックテスト（simulator/metrics/runner） | `data/price_cache.py`, `backtest/*` | **現行アルゴのベースライン勝率/期待値を出力** |
| **2** | 出口（タイムストップ/部分利確/建値/トレーリング）＋サイジング | `core/exit_rules.py`, `core/risk.py` | ベースライン比で期待値・DD改善を確認 |
| **3** | エントリーフィルタ（週足/指数/ADX/決算回避） | `core/regime.py`, `core/events.py`, ADX | 勝率改善を確認（約定率の低下とトレードオフ監視） |
| **4** | 最適化（ウォークフォワード） | `backtest/optimizer.py` | アウトオブサンプルで頑健性確認 |

---

## 9. 設計上の落とし穴（先に潰す）

- **ルックアヘッド・バイアス**：§4の厳守事項。指標は `df[:t]`、約定は翌バー以降の高安。
- **データ制約**：現状 `period="6mo"`（[`core/data_client.py`](../core/data_client.py)）。~5年へ拡張し、DBキャッシュでレート制限を回避。週足は十分な本数を確保。
- **約定の近似**：ギャップ始値で不利約定し得る → スリッページ（`slippage_atr`）とギャップを保守的にモデル化。SBI手数料も任意で。
- **過最適化**：パラメータを増やしすぎない。ウォークフォワード＆アウトオブサンプル必須。少数の意味あるパラメータに絞る。
- **決算データの信頼性**：yfinance の earnings date は欠損あり → 取得不可時は「除外しない」フォールバック方針。
- **ロングオンリー前提**：現行どおり空売りは扱わない（[`core/orders.py`](../core/orders.py) の long-only 制約を維持）。

---

## 10. 実装着手前に必要なら確定する残課題

- 部分利確の R地点・割合（推奨：1R で 50% 利確、残りトレーリング）— Phase 2 でバックテストにより調整。
- ウォークフォワードの窓幅（学習期間/検証期間）— Phase 4 で決定。
- 手数料・スリッページの具体値 — Phase 1 で保守的な初期値、以降キャリブレーション。
