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


def get_screening_universe() -> list[str]:
    """スクリーニング対象コードを DB ウォッチリストから取得する。

    DB が空または存在しない場合は config.py の SCREENING_UNIVERSE にフォールバック。
    定期実行では毎サイクル呼び直すことで Webアプリ編集が即反映される。
    """
    try:
        from data.db import get_connection, db_path
        if os.path.exists(db_path()):
            from data.repository import watchlist_codes
            conn = get_connection()
            codes = watchlist_codes(conn)
            conn.close()
            if codes:
                return codes
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"ウォッチリストのDB読込に失敗、フォールバックします: {e}")
    return list(SCREENING_UNIVERSE)

# ──────────────────────────────────────────
# スクリーニング対象ユニバース（日本株）
# ──────────────────────────────────────────
# 選定方法: 主力・高流動性の広い候補プール（電機/機械・銀行・不動産・AI半導体・
# 商社・重工/防衛など）を5年バックテストし、PF≥1.1・決済数≥15・流動性クリアの
# 銘柄のみ採用（2026-06時点、レジームフィルタOFFで純粋な銘柄品質を測定）。
# 詳細手順は backtest.runner / 候補選別ロジックを参照。コード昇順。
SCREENING_UNIVERSE_JP = [
    "1605", "1801", "1802", "1803", "1812", "1878", "1928", "2432", "2502", "2768",
    "2802", "2897", "2914", "3003", "3382", "3407", "4021", "4062", "4063", "4188",
    "4502", "4503", "4519", "4901", "5019", "5020", "5101", "5631", "5713", "5802",
    "6098", "6103", "6113", "6146", "6178", "6268", "6301", "6326", "6361", "6471",
    "6503", "6504", "6526", "6645", "6762", "6770", "6857", "6971", "6981", "7011",
    "7012", "7013", "7182", "7203", "7211", "7259", "7272", "7453", "7735", "7974",
    "8002", "8031", "8035", "8053", "8058", "8267", "8308", "8316", "8331", "8411",
    "8591", "8593", "8601", "8604", "8725", "8750", "8766", "8801", "8802", "8830",
    "9020", "9101", "9104", "9107", "9433", "9501", "9531", "9766", "9984",
]

# ──────────────────────────────────────────
# スクリーニング対象ユニバース（米国株）
# ──────────────────────────────────────────
# 選定方法は日本株と同じ（5年バックテストでPF≥1.1・決済数≥15・流動性クリア）。
# 米国株は最低株価フロアをドル建て（min_price_us）で判定するため、$300未満でも
# 高流動の主力株（AAPL/NVDA/XOM 等）が正しく対象になる。コード昇順。
SCREENING_UNIVERSE_US = [
    "AAPL", "ABBV", "AMD", "AMGN", "ANET", "ARM", "AVGO", "AXP", "BA", "BLK",
    "CAT", "COP", "COST", "CRWD", "CSCO", "CVX", "DDOG", "DELL", "DIS", "EMR",
    "ETN", "GE", "GILD", "GOOGL", "GS", "HD", "IBM", "INTC", "KLAC", "LLY",
    "LMT", "LRCX", "META", "MPC", "MRK", "MRVL", "MSFT", "MU", "NET", "NUE",
    "NVDA", "ORCL", "OXY", "PANW", "PEP", "PG", "PH", "PLTR", "PM", "PSX",
    "RTX", "SCHW", "SMCI", "SNDK", "STX", "TPL", "TSLA", "TSM", "TXN", "UBER",
    "VLO", "VRTX", "WDC", "WFC", "WMB", "WMT", "XOM",
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
    "breakout_lookback":    30,    # 高値・安値ブレイクアウト判定期間（Phase8: 20→30。だましブレイク減・勝率↑）
    "min_price":            300,   # 監視対象の最低株価（日本株=円。ボロ株除外）
    "min_price_us":         5,     # 米国株の最低株価（ドル。ペニー株除外。¥300をドルに適用すると主力株が落ちるため分離）
    "min_avg_volume":       100_000,  # 最低平均出来高（株数。市場非依存）
}

# ──────────────────────────────────────────
# 注文プラン（指値・損切り・利確）の計算パラメータ
# ──────────────────────────────────────────
# ATR（平均日中値幅）を基準に具体価格を算出する。
TRADE_PLAN_CONFIG = {
    "atr_entry_pullback": 0.5,   # 指値を現値から何ATR離すか（押し目/戻りの深さ）
    "atr_stop_mult":      2.5,   # 損切りを指値から何ATR離すか（Phase8: 2.0→2.5。ノイズでの早期損切りを回避）
    "reward_risk_ratio":  3.0,   # 利確 = リスク幅 × この倍率（Phase8: 2.0→3.0。勝ちを伸ばす）
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
        "sector":   0.15,   # 業種トレンド＋業種内相対強度（SECTOR_CONFIG・業種データがある時のみ寄与）
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
#
# ⚠️ 米国株（market="US"）は SBI で OCO/IFD/IFDOCO が使えないため、上記設定に
#    関わらず自動的に「単発の指値/逆指値エントリー＋約定後に手動で置く損切り・
#    利確の参考価格」に切り替わる（core.orders が市場で分岐）。
# 執行条件: 指値=「条件なし」（ザラ場中有効）、逆指値=「成行」（トリガー後に確実
#    に約定。損切り・ブレイク追随を優先）を通知に明示する。
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
    # 追跡用にシグナルを1件ずつ個別通知し、message_id を保存する（Discord双方向Bot用）。
    # 有効化すると ✅=約定 / ❌=見送り のリアクションで状態を更新できる。
    "per_signal_tracking": False,
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
    "partial_tp_r":      0.8,   # 第1利確の R 地点（Phase8: 1.0→0.8。早めに利を確保し勝率↑・DD↓）
    "partial_tp_pct":    0.5,   # 第1利確で利確する割合
    "move_to_breakeven": True,  # 部分利確後に残りのストップを建値へ
    "trail_atr_mult":    1.5,   # ATRトレーリングの幅（Phase8: 2.0→1.5。トレンド時に利益を早く固定）
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
# 業種（セクター）スコア設定（core/sector.py + scoring._score_sector が参照）
# ──────────────────────────────────────────
# 「銘柄が属する業種が上昇トレンドか」「業種内で相対的に強いか」を合議スコアの
# sector コンポーネント(-1〜+1)として加点する。業種分類は J-Quants 無料版(JP)/
# yfinance(US) から取得して DB(sectors) にキャッシュ済みのものを使い、トレンド自体は
# ユニバース構成銘柄の日足から合成インデックスを作って算出する（価格の追加取得なし）。
#   enabled が False、または業種データ/構成数が不足する銘柄は sector 成分を出さず
#   従来スコアに無害フォールバックする（compute_consensus が総重みで正規化するため）。
SECTOR_CONFIG = {
    "enabled":          True,
    "grouping":         "sector17",  # 合成インデックスのグルーピング（JP=17業種 / US=yfinance sector）
    "index_ma":         50,          # 合成業種インデックスの移動平均期間（トレンド判定）
    "ma_slope_lookback": 10,         # 業種インデックスの傾きを見る期間
    "rs_lookback":      60,          # 相対強度を測るリターンの参照期間（営業日）
    "rs_scale":         0.10,        # 相対強度の正規化幅（業種比+10%アウトパフォームで+1.0）
    "min_constituents": 3,           # 合成インデックスに必要な最低構成銘柄数（未満は成分なし）
    "trend_weight":     0.5,         # sector 成分内での「業種トレンド」の比重
    "rs_weight":        0.5,         # sector 成分内での「相対強度」の比重
}

# ──────────────────────────────────────────
# 税金設定（損益計算で参照）
# ──────────────────────────────────────────
# 上場株式等の譲渡益に対する課税（申告分離課税）。
#   所得税 15% + 復興特別所得税 0.315% + 住民税 5% = 20.315%
# 利益（譲渡益）にのみ課税され、損失には課税されない。
# 同一通貨グループ内の損益は通算してから課税対象を算出する（損益通算）。
TAX_CONFIG = {
    "capital_gains_rate": 0.20315,
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
    "slippage_atr":           0.1,    # 約定スリッページ（ATR の 10%）。アクティブプランは手数料0円だが市場インパクトを想定
    # 注: SBI アクティブプランは1日100万円以下の約定は手数料0円のため fee_pct=0.0 で問題ない
    "min_abs_score":          0,      # 通知対象の最小絶対スコア（0=フィルタ無効）
}

# ──────────────────────────────────────────
# ウォークフォワード最適化設定（Phase 4）
# ──────────────────────────────────────────
# is_years + oos_years <= 5（history期間）を守ること。
# ステップを1年にすると (5 - is_years) 個のウィンドウが生成される。
OPTIMIZE_CONFIG = {
    "is_years":   3,      # インサンプル期間（年）
    "oos_years":  1,      # アウトオブサンプル期間（年）
    "step_years": 1,      # ウィンドウのスライド幅（年）
    "objective":  "profit_factor",   # 最大化目標: profit_factor / avg_r / win_rate
    # 探索するパラメータグリッド
    # ※ 組み合わせ数 = 各リストの積 → 10〜100 程度に抑えること
    "param_grid": {
        "min_abs_score":      [0, 20, 40],     # スコアフィルタ閾値
        "trail_atr_mult":     [1.5, 2.0, 2.5], # ATRトレーリング幅
        "partial_tp_r":       [0.8, 1.0, 1.2], # 第1利確 R 地点
        "breakout_lookback":  [15, 20, 30],    # ブレイクアウト判定期間
    },
}

# ──────────────────────────────────────────
# 調整可能パラメータの永続上書き（settings テーブル → 実行時マージ）
# ──────────────────────────────────────────
# Webで編集した値を stock.db(settings) に保存し、各 config セクションの
# getter（get_screening_config 等）がデフォルトに上書きして返す。スキャン/
# バックテストはこの getter 経由で読むため、編集が次回実行に即反映される。
#
# PARAM_SECTIONS: 調整可能パラメータ → ライブで参照される config セクション名。
#   ※ min_abs_score のライブ参照先は SCORING_CONFIG（screener.engine が scoring から読む）。
#     バックテストでは simulator が backtest_cfg から読むため runner 側で補正する。
PARAM_SECTIONS = {
    "atr_entry_pullback":  "TRADE_PLAN_CONFIG",
    "atr_stop_mult":       "TRADE_PLAN_CONFIG",
    "reward_risk_ratio":   "TRADE_PLAN_CONFIG",
    "trail_atr_mult":      "EXIT_CONFIG",
    "partial_tp_r":        "EXIT_CONFIG",
    "partial_tp_pct":      "EXIT_CONFIG",
    "move_to_breakeven":   "EXIT_CONFIG",
    "min_abs_score":       "SCORING_CONFIG",
    "breakout_lookback":   "SCREENING_CONFIG",
    "ma_short":            "SCREENING_CONFIG",
    "ma_long":             "SCREENING_CONFIG",
    "rsi_period":          "SCREENING_CONFIG",
    "rsi_oversold":        "SCREENING_CONFIG",
    "rsi_overbought":      "SCREENING_CONFIG",
    "volume_spike_ratio":  "SCREENING_CONFIG",
    "weekly_trend_filter": "REGIME_CONFIG",
    "adx_min":             "REGIME_CONFIG",
    "index_ma":            "REGIME_CONFIG",
    "account_size":        "RISK_CONFIG",
    "risk_per_trade_pct":  "RISK_CONFIG",
    "max_positions":       "RISK_CONFIG",
}


def load_param_overrides() -> dict:
    """settings テーブルから保存済みのパラメータ上書きを読む。無ければ {}。

    DB が無い・読めない場合はデフォルト運用（空 dict）にフォールバックする。
    """
    try:
        from data.db import get_connection, db_path
        if not os.path.exists(db_path()):
            return {}
        from data.repository import get_param_overrides
        conn = get_connection()
        try:
            return get_param_overrides(conn)
        finally:
            conn.close()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"パラメータ上書きの読込に失敗しました: {e}")
        return {}


def _effective_section(section_name: str, overrides: dict | None = None) -> dict:
    """指定セクションのデフォルトに、そのセクション宛の上書きをマージして返す。"""
    base = dict(globals()[section_name])
    ov = load_param_overrides() if overrides is None else overrides
    for param, value in ov.items():
        if PARAM_SECTIONS.get(param) == section_name:
            base[param] = value
    return base


def get_screening_config() -> dict:
    return _effective_section("SCREENING_CONFIG")


def get_scoring_config() -> dict:
    return _effective_section("SCORING_CONFIG")


def get_trade_plan_config() -> dict:
    return _effective_section("TRADE_PLAN_CONFIG")


def get_exit_config() -> dict:
    return _effective_section("EXIT_CONFIG")


def get_regime_config() -> dict:
    return _effective_section("REGIME_CONFIG")


def get_sector_config() -> dict:
    return _effective_section("SECTOR_CONFIG")


def get_risk_config() -> dict:
    return _effective_section("RISK_CONFIG")
