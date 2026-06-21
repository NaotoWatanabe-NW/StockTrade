"""
設定ファイル

SBI証券では個人向けAPIが提供されていないため、
このツールは「分析・通知の自動化」に特化し、発注は手動で行う設計です。

データソース: yfinance（Yahoo!ファイナンス、無料・登録不要）
通知先     : Discord Webhook
対応市場   : 日本株（東証）/ 米国株。銘柄コードから自動判定します
             （数字のみ→東証、英字→米国。明示する場合は market="JP"/"US"）
"""

import os
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    """プロジェクト直下の .env を読み、未設定の環境変数だけ補完する。

    依存追加を避けるための最小実装（KEY=VALUE 形式、# はコメント）。
    既に export 済みの環境変数は上書きしない。.env はgit管理外。
    """
    env_path = Path(__file__).with_name(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv()

# ──────────────────────────────────────────
# Discord通知設定
# ──────────────────────────────────────────
# Webhook URLは秘密情報。.env か環境変数で渡す（config.pyには直書きしない）
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# ──────────────────────────────────────────
# 保有銘柄（売買シグナル監視＋含み損益計算の対象）
# ──────────────────────────────────────────
#   code      : 証券コード（東証）/ ティッカー（米国）
#   avg_price : 取得単価（建値）。含み損益の計算に使用
#   shares    : 保有株数。あれば含み損益を金額でも表示
#   market    : 省略可。"JP"/"US"。省略時はcodeから自動判定
def get_holdings() -> list:
    """保有銘柄を SQLite(stock.db) から取得する。

    Webアプリで編集した内容が監視ツールに即反映されるよう、
    定期実行では毎サイクルこの関数を呼んで最新を読む。
    DBが存在しない・空の場合は空リストを返す。
    """
    try:
        from data.db import get_connection, db_path
        if not os.path.exists(db_path()):
            return []
        from data.repository import list_holdings
        conn = get_connection()
        rows = list_holdings(conn)
        conn.close()
        return rows
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"DBからの保有読込に失敗しました: {e}")
        return []


# 後方互換: import 時点のスナップショット（定期実行では get_holdings() を都度使う）
HOLDINGS = get_holdings()

# ──────────────────────────────────────────
# スクリーニング対象ユニバース（日本株）
# ──────────────────────────────────────────
SCREENING_UNIVERSE_JP = [
    "7203", "7267", "7269",  # 自動車
    "6758", "6861", "6920",  # 電機・半導体
    "8306", "8316", "8411",  # 金融
    "9984", "9433", "9434",  # 通信
    "4063", "4502", "4519",  # 化学・医薬
    "6501", "6503", "6594",  # 重電・モーター
    "8035", "6857", "6981",  # 半導体製造装置
    "9983", "3382", "8267",  # 小売
    "7011", "7012", "7013",  # 重工業
    "5401", "5713", "5108",  # 素材
]

# ──────────────────────────────────────────
# スクリーニング対象ユニバース（米国株）
# ──────────────────────────────────────────
SCREENING_UNIVERSE_US = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",   # メガテック
    "NVDA", "AMD", "AVGO", "TSM", "MU",        # 半導体
    "TSLA", "JPM", "V", "MA", "BRK-B",         # その他大型
    "XOM", "CVX", "JNJ", "PG", "KO",           # ディフェンシブ
]

# 実際にスキャンするユニバース（日本＋米国を結合）
SCREENING_UNIVERSE = SCREENING_UNIVERSE_JP + SCREENING_UNIVERSE_US

# ──────────────────────────────────────────
# テクニカルスクリーニング閾値
# ──────────────────────────────────────────
SCREENING_CONFIG = {
    "volume_spike_ratio":   2.0,   # 出来高が平均の何倍で「急増」とみなすか
    "volume_avg_period":    20,    # 出来高平均の計算期間
    "ma_short":             5,
    "ma_long":              25,
    "rsi_period":           14,
    "rsi_oversold":         30,
    "rsi_overbought":       70,
    "breakout_lookback":    20,    # 高値・安値ブレイクアウト判定期間
    "min_price":            300,   # 監視対象の最低株価（ボロ株除外。通貨混在のため緩め）
    "min_avg_volume":       100_000,  # 最低平均出来高（流動性確保）
}

# ──────────────────────────────────────────
# 注文プラン（指値・損切り・利確）の計算パラメータ
# ──────────────────────────────────────────
# ATR（平均日中値幅）を基準に具体価格を算出する。
TRADE_PLAN_CONFIG = {
    "atr_entry_pullback": 0.5,   # 指値を現値から何ATR離すか（押し目/戻りの深さ）
    "atr_stop_mult":      2.0,   # 損切りを指値から何ATR離すか
    "reward_risk_ratio":  2.0,   # 利確 = リスク幅 × この倍率（BUYのみ）
}

# ──────────────────────────────────────────
# 合議制スコアリング設定（複数指標の重み付き合算）
# ──────────────────────────────────────────
# weights を調整して精度を実験できる。合計は任意（内部で正規化）。
SCORING_CONFIG = {
    "weights": {
        "trend":    0.30,   # 移動平均の地合い
        "macd":     0.20,   # モメンタム
        "rsi":      0.15,   # 売られ/買われすぎ
        "volume":   0.15,   # 出来高の裏付け
        "breakout": 0.20,   # 高安レンジ内の位置
    },
    "thresholds": {"strong": 60, "weak": 20},  # |スコア| の閾値（強い/弱い）
    "rsi_low":            30,
    "rsi_high":           70,
    "ma_slope_lookback":  10,   # 長期MAの傾きを見る期間
    # スクリーニングで通知する最小スコア絶対値（0=フィルタ無効、例: 40で確度の高い候補のみ）
    "min_abs_score":      0,
}

# ──────────────────────────────────────────
# 注文タイプ設定（SBI証券の注文方法に変換して指示）
# ──────────────────────────────────────────
# entry_order_type（新規建て・買い増し時）:
#   "IFDOCO" … 1次で買い、約定後にOCO（利確指値＋損切り逆指値）を自動セット（推奨）
#   "IFD"    … 1次で買い、約定後に損切り逆指値のみ
#   "SIMPLE" … エントリーの指値/逆指値のみ（決済は手動）
# exit_order_type（保有ロング手仕舞い時）:
#   "OCO"    … 戻り売り指値（利確）＋ 撤退逆指値（損切り）を同時発注（推奨）
#   "STOP"   … 撤退の逆指値（損切り）のみ
ORDER_CONFIG = {
    "entry_order_type": "IFDOCO",
    "exit_order_type":  "OCO",
}

# ──────────────────────────────────────────
# 通知の絞り込み設定
# ──────────────────────────────────────────
NOTIFY_CONFIG = {
    # 出来高急増のみ等、方向性のない（注文プランが付かない）保有シグナルは通知しない
    "suppress_neutral_holdings": True,
    # 長期保有(long_term=True)の銘柄は売り/手仕舞いを通知せず、買い増しタイミングのみ通知
    #   ※ この挙動は long_term フラグ自体で常に有効（下のフラグはマスタースイッチ）
    "long_term_buy_only": True,
}

# ──────────────────────────────────────────
# スイング向けシグナル判定設定
# ──────────────────────────────────────────
SIGNAL_CONFIG = {
    "check_interval_minutes": 60,    # 監視間隔（分）。スイングなので高頻度不要
}

# ──────────────────────────────────────────
# 出口・リスク管理（Phase 2 で exit_rules.py が参照）
# ──────────────────────────────────────────
# time_stop_days はエントリー有効期限（15営業日）とは独立。
# 保有中の時間切れ手仕舞いを何営業日で行うか（SBI決済注文の期間指定に合わせる）。
EXIT_CONFIG = {
    "time_stop_days":    15,    # 保有中タイムストップ（営業日）
    "partial_tp_r":      1.0,   # 第1利確の R 地点
    "partial_tp_pct":    0.5,   # 第1利確で利確する割合
    "move_to_breakeven": True,  # 部分利確後に残りのストップを建値へ
    "trail_atr_mult":    2.0,   # ATRトレーリングの幅
}

# ──────────────────────────────────────────
# ポジションサイジング（Phase 2 で risk.py が参照）
# ──────────────────────────────────────────
# 口座サイズと許容リスク%から 1 トレードの株数を算出する推奨値。
# account_size は実際の口座残高に合わせて調整すること。
RISK_CONFIG = {
    "account_size":       1_000_000,  # 口座サイズ（円）
    "risk_per_trade_pct": 1.0,        # 1トレード許容リスク（口座の%）
    "max_positions":      5,          # 同時保有上限
}

# ──────────────────────────────────────────
# レジーム・上位足フィルタ（Phase 3 で regime.py が参照）
# ──────────────────────────────────────────
REGIME_CONFIG = {
    "jp_index":            "^N225",   # 日本株の代表指数（TOPIX: "^TOPX"も可）
    "us_index":            "^GSPC",   # 米国株の代表指数
    "index_ma":            50,        # 指数の移動平均期間（価格がMA上 = 強気レジーム）
    "weekly_trend_filter": True,      # 週足トレンドフィルタを有効にするか
    "adx_min":             20,        # ADX がこの値未満ならチョップ相場として見送り
}

# ──────────────────────────────────────────
# イベント回避（Phase 3 で events.py が参照）
# ──────────────────────────────────────────
EVENTS_CONFIG = {
    "avoid_earnings_within_days": 15,   # 決算発表が15営業日以内なら見送り
}

# ──────────────────────────────────────────
# バックテスト設定（backtest/ が参照）
# ──────────────────────────────────────────
BACKTEST_CONFIG = {
    "history":                "5y",   # 取得する履歴の期間
    "entry_order_valid_days": 15,     # エントリー注文の有効期限（営業日）
    "max_hold_bars":          20,     # 約定後の最長保有バー数（タイムストップ上限）
    "slippage_atr":           0.0,    # 約定スリッページ（ATR の倍率。0=なし）
    "min_abs_score":          0,      # 通知対象の最小絶対スコア（0=フィルタ無効）
}
