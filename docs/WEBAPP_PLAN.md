# Webアプリ機能拡張 詳細設計

> 本書は2つの機能拡張の詳細設計。**実装はフェーズ単位で進め、各フェーズ後にテスト緑を確認する。**
>
> 対象:
> - **機能①** シグナル追跡の入力強化（決済済み・期限切れ・購入株数の入力 → 取引記録・損益と連動）
> - **機能②** バックテストのWeb実行（パラメータ指定 → バックグラウンド実行 → ポーリング表示）

---

## 0. 確定した設計判断

| 項目 | 確定 | 理由 |
|------|------|------|
| ①の入力モデル | **trades を単一の真実とする**（約定/決済は `signal_id` 付き `trades` 行として記録） | 既存の自動算出機構 [`_on_signal_trade_change`](../data/repository.py) を全面再利用。`CLOSED`/`realized_r`/購入株数が自動。スキーマ変更ほぼ不要 |
| 取引の棲み分け | 取引記録ページ＝`signal_id=NULL` の**単独注文**、シグナル画面＝`signal_id`付きの**予測ベース注文** | どちらも同じ `trades` に入り、取引記録一覧・損益(PnL)に反映される（PnLはtrades基準なので自動連動） |
| ②の実行方式 | **バックグラウンド実行＋ポーリング** | 初回キャッシュ未構築でも実行が重くてもUIをブロックしない |
| ②のパラメータ範囲 | **フルのチューニング可能パラメータ** | 将来の「パラメータWeb編集」機能と統合する前提。`/defaults` で既定値を供給 |

---

## 1. 機能① シグナル追跡の入力強化

### 1-1. 現状の制約（コード上の根拠）

| 事実 | 根拠 |
|---|---|
| `CLOSED`・`realized_r`・購入株数は **`trades` の `signal_id` 紐付けから自動算出** | `_on_signal_trade_change()`：BUYあり→`TAKEN`、売却株数≥買付株数→`CLOSED`＋`realized_r=(平均売単価−平均買単価)/risk` |
| `add_trade`/`delete_trade` は `signal_id` 付きなら自動で上記を再計算 | `repository.py` |
| **取引記録UIに `signal_id` 入力欄が無い** → Webから紐付け不能 → `CLOSED`/`realized_r` に到達できない | `web/app/trades/page.tsx` |
| シグナル画面は `TAKEN`/`SKIPPED`/`OPEN` ボタンのみ（株数・約定単価・決済・期限切れ入力不可） | `web/app/signals/page.tsx` |
| `EXPIRED` はバッチ専用（`expire_stale_signals()`）。手動UI無し | `repository.py` |
| `sync_holding_from_trades` は **取引ルーター側**で呼ばれる（`add_trade` 自体は保有を同期しない） | `api/routers/trades.py` |

→ 自動算出機構は完成済み。**それを駆動する `trades` 紐付けがUIから到達不能**なのが原因。

### 1-2. データモデル方針
`signals` テーブルは変更しない。約定・決済は **`signal_id` 付き `trades` 行**として記録し、`_on_signal_trade_change` が `status`/`realized_r` を自動再計算。購入株数・平均約定単価は `trades` から集計表示。

### 1-3. バックエンド変更

**data/repository.py**
- `list_signals` / `get_signal` を拡張：`LEFT JOIN trades ON trades.signal_id = signals.id ... GROUP BY signals.id` で集計列を付与
  - `filled_shares`（BUY株数合計）, `sold_shares`（SELL株数合計）
  - `avg_fill_price`（BUY金額/BUY株数）, `avg_sell_price`
  - `remaining_shares`（= filled − sold）, `position_value`（= remaining × avg_fill_price）
- `list_trades` に `signal_id` フィルタ引数を追加（シグナルに紐付く約定一覧の取得用）
- `add_trade` / `delete_trade` / `_on_signal_trade_change`：**変更なし**

**api/routers/signals.py（新規エンドポイント）**
- `POST /api/signals/{id}/fill` … body `{shares, price, traded_at, fee?}`
  → `add_trade(side="BUY", signal_id=id, code/name/market=シグナルから自動補完, ...)`
  → `sync_holding_from_trades(conn, code)` を呼んで**保有も更新**（取引ルーターと同じ挙動）
  → 戻り値は更新後の `SignalOut`
- `POST /api/signals/{id}/close` … body 同上 → `side="SELL"` で同様。全株決済で自動 `CLOSED`＋`realized_r`
- `GET /api/signals/{id}/trades` … 紐付く約定一覧（取り消し表示用）
- 紐付き約定の削除は既存 `DELETE /api/trades/{trade_id}` を流用（`_on_signal_trade_change` が status を戻す）
- **期限切れ**は既存 `POST /api/signals/{id}/status` に `EXPIRED` を渡すだけ（API変更不要）

**api/schemas.py**
- `SignalOut` に集計列追加：`filled_shares`, `sold_shares`, `avg_fill_price`, `remaining_shares`, `position_value`
- `SignalFillIn { shares, price, traded_at, fee=0 }` を新設（fill/close 共通）

### 1-4. 設計上の判断
- **SELLシグナル（手仕舞い指示）** は `realized_r` 対象外。UIでは BUYシグナルのみ fill/close を出し、SELLは status操作のみ
- **保有との関係**：fillは `sync_holding_from_trades` で保有にも反映（実際に約定＝保有が増えた、という整合した挙動）。損益はtrades基準で自動反映
- 部分約定・分割決済は複数 trade 行で自然対応（既存ロジックがそのまま機能）

### 1-5. Web変更
**web/app/signals/page.tsx**
- シグナル表に列追加：**保有株数 / 平均約定単価 / 投資額**
- 各行を展開して：
  - 「約定を記録」フォーム（株数・単価・日）
  - 「決済を記録」フォーム（株数・単価・日）
  - 「期限切れ」ボタン
  - 紐付く約定の一覧＋削除

**web/app/trades/page.tsx**
- 一覧に**シグナル紐付けの可視化**（`signal_id` があればバッジ/リンク表示）。棲み分け（単独注文 vs 予測ベース）が一目で分かるようにする

**web/lib/api.ts**
- `signalFill`, `signalClose`, `getSignalTrades` を追加
- `Signal` 型に集計列を追加

---

## 2. 機能② バックテストのWeb実行

### 2-1. 現状の制約
- `runner.py:run(args)` は **argparse依存＋print出力＋同期実行**。Webから直接呼べない
- 初回はyfinance取得で重い（DBキャッシュ後は高速）。同期だとリクエストをブロックしうる
- 保存導線（`--save` → `save_backtest_run`）は既にある

### 2-2. runner のリファクタ（CLIとWebで共有）
`run(args)` から純粋実行関数を切り出す：

```python
def run_backtest(universe: str, *, regime: bool, no_partial_tp: bool,
                 min_score: float | None, param_overrides: dict | None,
                 save: bool = True, conn=None) -> dict:
    # cfg を構築（param_overrides を各 cfg にマージ）→ 銘柄ループ →
    # compute_metrics → save_backtest_run → {run_id, metrics}
```
- argparse/print は薄い `main()` に残し、`main()` は `run_backtest` を呼ぶだけ（**既存CLIテストは緑のまま**）
- `param_overrides`（フラット名→cfg）のマッピングは optimizer の `_PARAM_MAP` を全パラメータに拡張して共有：

| パラメータ | 行き先cfg |
|---|---|
| `atr_entry_pullback`, `atr_stop_mult`, `reward_risk_ratio` | trade_plan_cfg |
| `trail_atr_mult`, `partial_tp_r`, `partial_tp_pct` | exit_cfg |
| `min_abs_score` | scoring_cfg / backtest_cfg |
| `breakout_lookback`, `ma_short`, `ma_long`, `rsi_*` | screening_cfg |
| `weekly_trend_filter`, `adx_min` | regime_cfg |

- `params_snapshot` に**マージ後の全cfg**を保存（Webの既存 params JSON 表示でそのまま確認可能）
- Web実行は常に **DBキャッシュ経由**（`no_cache` 不可。yfinance暴発防止）

### 2-3. ジョブ管理（バックグラウンド＋ポーリング）
`backtest_runs` に既存 `_MIGRATIONS` パターンで2列追加：
- `status TEXT DEFAULT 'done'`（`running`/`done`/`error`）, `error TEXT`

フロー：
1. `POST /api/backtest/run` → `status='running'` 行を即 INSERT → `BackgroundTasks` で `run_backtest(...)` 実行予約 → **202 `{id, status:'running'}`** を即返す
2. BackgroundTask が完了時にその行へ metrics を UPDATE し `status='done'`（例外時 `status='error'`＋`error`）
3. Web は既存 `GET /api/backtest` をポーリング → `running` を「実行中」表示、`done` で結果、`error` で失敗表示

- FastAPI `BackgroundTasks` は同一プロセス内実行。ローカル単一ユーザ用途には十分（重い処理が1ワーカーを占有する点に留意。初回はCLIでキャッシュをウォームアップ推奨）

### 2-4. API追加
- `POST /api/backtest/run` … body `{ universe, regime, no_partial_tp, min_score, params:{...overrides} }`
- `GET /api/backtest/defaults` … 現行 config の調整可能パラメータ既定値を返す（フォーム初期値＋将来の「パラメータWeb編集」の土台）
- `GET /api/backtest` / `/{id}` … `status`/`error` を含めて返すよう拡張

### 2-5. Web変更
**web/app/backtest/page.tsx**
- 「新規バックテスト」フォーム：universe セレクト＋regime/partial_tpトグル＋min_score＋折りたたみで**フルパラメータ**（既定値は `/defaults` から取得）
- 送信 → `runBacktest` → 一覧をポーリングして `running`→`done` を反映
- 表に**状態列（実行中/完了/失敗）**を追加

**web/lib/api.ts**
- `runBacktest`, `getBacktestDefaults` を追加、`BacktestRun` 型に `status`/`error` を追加

---

## 3. テスト方針（CLAUDE.mdルール準拠：自明なテスト禁止・実装優先・記述的な名前）

**機能①**
- repo集計：部分約定→`TAKEN`＋`filled_shares`正、全決済→`CLOSED`＋`realized_r`正、fill削除でstatus復帰
- fill/close API統合、`EXPIRED` 遷移
- 取引の棲み分け：`signal_id` 付き約定がPnL・取引一覧に出ること

**機能②**
- `run_backtest` が小さな合成OHLC（yfinanceをモックせずキャッシュ注入）で metrics を返し、`params` に上書きが反映される
- `param_overrides` が実挙動を変える（`min_score` でシグナル数が減る）
- 背景ジョブが `done`/`error` を立てる

---

## 4. 実装フェーズ（小さく緑を保って進める）

| Phase | 内容 | 主な成果物 |
|-------|------|-----------|
| **A（①）** | repo集計＋fill/close/status＋signals UI＋trades一覧の紐付け可視化 | 自己完結・高価値・低リスク |
| **B（②-1）** | `run_backtest` 切り出し＋param_map拡張（既存CLIテスト緑維持） | runner リファクタ |
| **C（②-2）** | migration（status/error列）＋`POST /run`＋BackgroundTask＋`/defaults` | バックテストAPI |
| **D（②-3）** | バックテストUIフォーム＋ポーリング＋状態表示 | バックテストUI |

---

## 5. 将来機能との接続（参考）
- ②の `/defaults` とパラメータ上書きは、将来の**「シグナル検出パラメータのWeb編集」**の土台になる
- ①で蓄積する実約定・実現Rは、将来の**「約定率記録」「MLによる閾値最適化」**の教師データになる
