"""APIの入出力スキーマ（Pydantic）"""

from typing import Optional
from pydantic import BaseModel, Field


class HoldingIn(BaseModel):
    code: str
    name: Optional[str] = None
    avg_price: Optional[float] = None
    shares: Optional[float] = None
    market: Optional[str] = None          # "JP"/"US"/None(自動判定)
    long_term: bool = False


class HoldingOut(HoldingIn):
    id: int


class TradeIn(BaseModel):
    code: str
    name: Optional[str] = None
    side: str = Field(description="BUY または SELL")
    shares: float = Field(gt=0)
    price: float = Field(ge=0)
    fee: float = 0.0
    traded_at: str = Field(description="約定日 YYYY-MM-DD")
    note: Optional[str] = None
    signal_id: Optional[int] = None       # 紐付くシグナル（任意）


class TradeOut(TradeIn):
    id: int


class PnlRow(BaseModel):
    code: str
    name: Optional[str] = None
    buy_shares: float
    sell_shares: float
    remaining_shares: float
    avg_cost: float
    buy_amount: float
    sell_amount: float
    fee_total: float
    realized: float


class PnlSummaryRow(BaseModel):
    currency: str
    label: str
    realized: float            # 税引前の実現損益合計
    tax: float                 # 税額（利益>0のときのみ課税）
    realized_after_tax: float  # 税引後の実現損益合計
    tax_rate: float


class PnlResponse(BaseModel):
    rows: list[PnlRow]
    summary: list[PnlSummaryRow]


class WatchlistIn(BaseModel):
    code: str
    name: Optional[str] = None
    market: Optional[str] = None
    note: Optional[str] = None


class WatchlistOut(WatchlistIn):
    id: int
    created_at: str


class BacktestRunOut(BaseModel):
    id: int
    run_at: str
    universe: str
    n_signals: Optional[int] = None
    n_filled: Optional[int] = None
    fill_rate: Optional[float] = None
    n_closed: Optional[int] = None
    win_rate: Optional[float] = None
    avg_r: Optional[float] = None
    profit_factor: Optional[float] = None
    max_drawdown_r: Optional[float] = None
    time_stop_rate: Optional[float] = None
    params: Optional[str] = None          # JSON 文字列
    sharpe: Optional[float] = None
    annual_return_pct: Optional[float] = None
    equity_curve: Optional[str] = None    # JSON 文字列（詳細取得時のみ）
    status: str = "done"                  # running / done / error
    error: Optional[str] = None           # status='error' のときの失敗内容


class BacktestRunRequest(BaseModel):
    """Web からのバックテスト実行リクエスト。"""
    universe: str = "ALL"                          # "JP" / "US" / "ALL"
    regime: bool = True                            # レジームフィルタの有効/無効
    no_partial_tp: bool = False                    # 部分利確を無効化するか
    min_score: Optional[float] = None              # スコア絶対値フィルタ（None=既定）
    params: dict = Field(default_factory=dict)     # 個別パラメータ上書き（フラット名→値）


class SettingItem(BaseModel):
    """調整可能パラメータ1件の現在状態。"""
    param: str
    section: str
    # bool を int より先に置く（True は int でもあるため）。整数パラメータの型を保つ。
    value: bool | int | float           # 現在の有効値（デフォルト⊕上書き）
    default: bool | int | float         # 上書きを除いたデフォルト値
    overridden: bool                    # Webで上書きされているか


class SettingsUpdateIn(BaseModel):
    """パラメータ上書きの保存リクエスト（部分更新）。"""
    values: dict[str, bool | int | float] = Field(default_factory=dict)


class PortfolioHeat(BaseModel):
    open_positions: int
    max_positions: int
    risk_per_trade_pct: float
    heat_pct: float        # 現在の使用リスク%（= open_positions × risk_per_trade_pct）
    heat_max_pct: float    # 最大リスク%（= max_positions × risk_per_trade_pct）


class SizingSuggestion(BaseModel):
    """1 OPEN シグナルに対する推奨株数（固定リスク%サイジング）。"""
    signal_id: int
    code: str
    name: Optional[str] = None
    market: Optional[str] = None
    score: Optional[float] = None
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    lot_size: int
    suggested_shares: int
    investment: float          # 株数 × entry
    risk_amount: float         # 株数 × (entry − stop) = 実リスク額


class SizingResponse(BaseModel):
    account_size: float
    risk_per_trade_pct: float
    max_positions: int
    open_positions: int
    remaining_slots: int       # max_positions − 現在保有数（下限0）
    heat_pct: float            # 現在の使用リスク%（保有数 × risk%）
    suggestions: list[SizingSuggestion]


class SignalOut(BaseModel):
    id: int
    generated_at: str
    code: str
    name: Optional[str] = None
    market: Optional[str] = None
    side: str
    signal_types: Optional[str] = None     # JSON 文字列
    score: Optional[float] = None
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    risk: Optional[float] = None
    entry_kind: Optional[str] = None
    order_type: Optional[str] = None
    status: str
    realized_r: Optional[float] = None
    notes: Optional[str] = None
    # 紐付く約定(trades)の集計（建玉状況の表示用）
    filled_shares: float = 0      # 約定済み買付株数の合計
    sold_shares: float = 0        # 決済済み売付株数の合計
    avg_fill_price: Optional[float] = None   # 平均約定単価（買付）
    avg_sell_price: Optional[float] = None   # 平均決済単価（売付）
    remaining_shares: float = 0   # 残建玉株数（= filled - sold）
    position_value: Optional[float] = None   # 残建玉の投資額（残株 × 平均約定単価）


class SignalStatusIn(BaseModel):
    status: str = Field(description="OPEN/TAKEN/CLOSED/SKIPPED/EXPIRED")


class SignalFillIn(BaseModel):
    """シグナルに紐付く約定（建玉=fill / 決済=close 共通）の入力。"""
    shares: float = Field(gt=0)
    price: float = Field(ge=0)
    traded_at: str = Field(description="約定日 YYYY-MM-DD")
    fee: float = 0.0


class ScoreCalibrationBucket(BaseModel):
    score_lo: float
    score_hi: float
    n_signals: int            # バケットに属する確定済みシグナル数
    n_entered: int            # うち計画 entry に到達した数（NO_ENTRY を除く）
    entry_rate: Optional[float] = None
    n_target: int
    n_stop: int
    n_timeout: int
    win_rate: Optional[float] = None    # 約定分の実現R>0 割合
    avg_r: Optional[float] = None       # 約定分の平均実現R
    avg_mfe_r: Optional[float] = None   # 平均の最大含み益（R）


class SignalAttribution(BaseModel):
    total: int
    open: int
    taken: int
    closed: int
    skipped: int
    expired: int
    take_rate: Optional[float] = None
    live_closed: int
    live_win_rate: Optional[float] = None
    live_avg_r: Optional[float] = None
    bt_universe: Optional[str] = None
    bt_run_at: Optional[str] = None
    bt_win_rate: Optional[float] = None
    bt_avg_r: Optional[float] = None
    bt_fill_rate: Optional[float] = None
