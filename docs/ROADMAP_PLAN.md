# 将来機能 詳細設計（ロードマップ）

> 本書は「組織化」で挙げた将来機能の詳細設計。**今回は ML 以外の4機能を実装し、ML は構想のみ残す。**
> 実装は機能単位で進め、各機能後にテスト緑を確認する。先行実装（Phase A〜D）の資産を土台にする。

## 確定した設計判断

| 項目 | 確定 | 理由 |
|------|------|------|
| パラメータDB化の反映 | **実行時マージ**（settingsテーブル＋config getter） | Web編集が次回スキャン/バックテストに即反映。再起動不要。`get_holdings`/`get_screening_universe` と同じ既存パターン |
| 推奨株数の口座残高 | **RISK_CONFIG.account_size** を使う | 価格取得不要で確実。熱量計算・Discord通知と整合。①のDB化でWeb編集可になる |
| ライブ約定率の期限切れトリガ | **読み取り時に遅延実行（lazy）** | 追加インフラ不要・常に最新。`expire_stale_signals` を一覧/集計エンドポイントの先頭で叩く |
| Discord Bot | **API側対応＋Bot雛形** | gateway受信はトークン必須でテスト不可。API側（message_id紐付け＋既存status/fill API）はテスト可能にし、Botは雛形を同梱 |

---

## 機能1. パラメータのDB化＋Web編集（実行時マージ）

### 1-1. 現状
- 調整可能パラメータは [config.py](../config.py) の各 dict（`SCREENING_CONFIG`/`SCORING_CONFIG`/`TRADE_PLAN_CONFIG`/`EXIT_CONFIG`/`RISK_CONFIG`/`REGIME_CONFIG`）にハードコード。
- ライブスキャンは [main.py:167-178](../main.py) で各 dict をエンジンに渡す。バックテストは [runner.run_backtest](../backtest/runner.py) が `dict(config.SECTION)` で読む。
- 既に `current_param_defaults()`（runner）が調整可能パラメータ→現在値を返す。Phase C の `GET /api/backtest/defaults` がそれを使う。

### 1-2. 設計
- **storage**: `settings` テーブル（key-value）。`key='param_overrides'`、`value=JSON`（フラットな {param: value} 上書き）。フラットキーは Phase B/C のバックテスト param と同一キースペースで統一。
- **パラメータ→セクション対応**を [config.py](../config.py) に `PARAM_SECTIONS`（`{param: "SCREENING_CONFIG"|...}`）として定義（runner の `_PARAM_TARGETS` の正準版をここへ移し、runner はこれを参照）。
  - `min_abs_score` のライブ参照先は SCORING_CONFIG（[engine.py](../screener/engine.py) が scoring から読む）。バックテストでは simulator が backtest_cfg から読むため run_backtest 側で補正する。
  - `account_size`/`risk_per_trade_pct`/`max_positions` は RISK_CONFIG（機能2のサイジングが参照）。
- **config getter**: `get_screening_config()` 等を追加。`dict(DEFAULT_SECTION)` に「そのセクションを行き先とする上書き」をマージして返す。DB 不在/空ならデフォルトのまま。
- **起点を getter 化**: [main.py](../main.py) のエンジン構築、[runner.run_backtest](../backtest/runner.py) のベース cfg、[portfolio.py](../api/routers/portfolio.py) の RISK 参照を getter 経由にする。
- **API**: `GET /api/settings`（有効値＝デフォルト⊕上書き、各paramの override 有無付き）、`PUT /api/settings`（上書きを保存・検証）、`DELETE /api/settings/{param}`（1件リセット）。
- **Web**: `/settings`（パラメータ）画面。セクション別にグルーピング表示、編集・保存・既定に戻す。Nav に追加。

### 1-3. テスト
- settings repo: upsert/取得/削除のラウンドトリップ。
- getter: 上書きが該当セクションにのみ反映され他は既定のまま／未知キー拒否。
- API: PUT→GET で有効値が変わる／無効キーは400／DELETEで既定に戻る。
- 連携: 上書き保存後に `run_backtest` の `params` が反映（実行時マージの確認）。

---

## 機能2. 口座残高→推奨株数のWeb表示

### 2-1. 現状
- [risk.calc_shares](../core/risk.py) が固定リスク%方式で株数を算出（既存・テスト済み）。`RISK_CONFIG`（account_size/risk_per_trade_pct/max_positions）も定義済み。
- OPEN シグナルは `entry_price`/`stop_price`/`risk` を持つ（機能Aで集計列も追加済み）。

### 2-2. 設計
- **API**: `GET /api/portfolio/suggestions` を新設。
  - account_size = `get_risk_config().account_size`、risk% = risk_per_trade_pct。
  - OPEN かつ BUY のシグナルを対象に、各シグナルへ `calc_shares(account_size, risk%, entry, stop, lot)` で推奨株数を算出。lot は `lot_size_for_market(market)`。
  - 併せて投資額（株数×entry）、現在の保有数・残り枠（max_positions − 保有数）、ヒート（保有数×risk%）も返す。
- **Web**: シグナル画面の上部、または `/settings`/ダッシュボードに「推奨サイジング」カード。OPEN BUY 一覧＋推奨株数・投資額・残り枠を表示。1クリックで該当シグナルの「約定を記録」フォームに株数をプリフィル（機能Aと接続）。

### 2-3. テスト
- suggestions: account_size と OPEN BUY シグナルから期待株数（lot 丸め込み）を返す／SELL・非OPENは除外／残り枠・ヒートが正しい。

---

## 機能3. ライブ約定率の自動記録（期限切れ自動遷移）

### 3-1. 現状
- `expire_stale_signals` は [main.py の _persist_signals](../main.py) でスキャン時のみ実行。Webだけ運用だと OPEN が滞留し約定率が過大評価される。
- `signal_attribution` は既に `take_rate`（終局シグナル中の約定到達率）を算出。

### 3-2. 設計
- **lazy 期限切れ**: `GET /api/signals`・`/attribution` の冒頭で `expire_stale_signals(conn, valid_days)` を呼ぶ（idempotent な UPDATE。OPEN かつ generated_at が暦日 valid_days×1.5 を超えたものを EXPIRED 化）。これで Web 運用でも約定率が自動更新される。
- valid_days は `get_backtest_config()`/`BACKTEST_CONFIG.entry_order_valid_days`。
- **可視化**: シグナル画面のアトリビューションカードに「約定到達率（ライブ）」を明示（既存 take_rate）。スコア帯別の約定率は機能Aの calibration に既出。追加で fill/no-fill 件数の内訳表示を補強。

### 3-3. テスト
- API: 古い OPEN を作って `GET /api/signals` を叩くと EXPIRED に遷移している／新しい OPEN は維持される。
- take_rate が期限切れ反映後に正しくなる。

---

## 機能4. Discord Bot 双方向化（API側対応＋Bot雛形）

### 4-1. 現状
- [discord_notifier.py](../notifier/discord_notifier.py) は Webhook 送信のみ。スクリーニング結果は全銘柄まとめて1通。受信不可。

### 4-2. 設計
- **per-signal 通知＋message_id 保存**:
  - signals テーブルに `discord_message_id TEXT` を追加（マイグレーション）。
  - 新規 OPEN シグナルを Discord に1件ずつ送信（Webhook を `?wait=true` で叩き、返却 JSON の `id` を保存）。リアクション対象を1:1に対応付ける。
  - 既存のまとめ通知は残し、追跡対象のシグナルのみ個別通知を追加（設定で切替）。
  - repo: `set_signal_message_id`、`get_signal_by_message_id`。
- **Bot 雛形（双方向）**: `notifier/discord_bot.py`（discord.py、別プロセス常駐 `run_bot.sh`）。
  - `on_raw_reaction_add`: message_id→signal を引き、✅=`POST /api/signals/{id}/fill`（既定株数 or 推奨株数）、❌=`status=SKIPPED` を**既存 API 経由**で実行。
  - トークンは `.env` の `DISCORD_BOT_TOKEN`、API ベースは `API_BASE`（既定 http://localhost:8000）。未設定なら起動時に明示エラー。
  - discord.py は任意依存（requirements に追記、未インストールでも本体は動く）。
- **テスト可能な範囲**: message_id の保存/逆引き repo、個別通知が message_id を保存すること（requests をスタブ）、Bot のリアクション→API 呼び出しの**ディスパッチ関数**を純粋関数として切り出してテスト（discord.py 本体はモックしない＝ディスパッチに raw 値を渡す）。

### 4-3. テスト
- repo: `set_signal_message_id`/`get_signal_by_message_id` のラウンドトリップ。
- 通知: 個別通知時に wait=true の戻り id が signals に保存される（requests をスタブ）。
- ディスパッチ: ✅/❌ の絵文字 → 期待する API 呼び出し（fill/skip）に振り分けられる。

---

## 機能5. ML による閾値最適化（**構想のみ・本回は未実装**）

### 5-1. 方針
- シグナル検出自体はテクニカル分析のまま固定。**閾値・係数パラメータ（しきい値）を学習**で最適化する。
- 教師データは機能A/3で蓄積する `signal_outcomes`（予測→実勢の決着）と実約定（trades 紐付け）。

### 5-2. 想定アプローチ（順に検証）
1. **キャリブレーション学習**: `score → 勝率/期待R` をロジスティック回帰等で学習し、`min_abs_score` の最適閾値をデータで決める（既存 `score_calibration` を特徴量化）。
2. **ベイズ最適化**: Phase B/C のグリッドサーチ（`run_backtest` + ウォークフォワード）を、ベイズ最適化（Optuna 等）に置換し、`atr_*`/`trail_atr_mult`/`partial_tp_r`/`breakout_lookback` 等を効率探索。
3. **検証**: 必ずアウトオブサンプル（ウォークフォワードの OOS 窓）で頑健性を確認。過最適化が最大リスク。

### 5-3. 接続点（既に用意済み）
- 機能1の `settings`（学習結果のパラメータを書き戻す先）。
- Phase B/C の `run_backtest`/`_apply_param_overrides`（目的関数の評価器）。
- 機能A/3の `signal_outcomes`/実約定（教師データ）。

### 5-4. 未確定（着手時に決める）
- ライブラリ選定（Optuna / scikit-learn）。
- 目的関数（profit_factor / 期待R / Sharpe）と制約（最小トレード数）。
- 再学習頻度と人手承認フロー（自動で settings を上書きするか、提案に留めるか）。

---

## 実装順（依存と費用対効果）

| 順 | 機能 | 依存 |
|----|------|------|
| 1 | パラメータDB化＋Web編集 | 後続すべての土台（account_size 含む） |
| 2 | 口座残高→推奨株数 | 機能1（account_size のDB化）＋既存 risk.py |
| 3 | ライブ約定率の自動記録 | 単独・小。既存 expire/attribution |
| 4 | Discord Bot双方向（API側＋雛形） | 機能2（推奨株数で約定登録）と相性 |
| – | ML 閾値最適化 | 構想のみ（本回未実装） |
