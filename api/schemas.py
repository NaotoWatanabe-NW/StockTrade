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


class PortfolioHeat(BaseModel):
    open_positions: int
    max_positions: int
    risk_per_trade_pct: float
    heat_pct: float        # 現在の使用リスク%（= open_positions × risk_per_trade_pct）
    heat_max_pct: float    # 最大リスク%（= max_positions × risk_per_trade_pct）


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


class SignalStatusIn(BaseModel):
    status: str = Field(description="OPEN/TAKEN/CLOSED/SKIPPED/EXPIRED")


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
