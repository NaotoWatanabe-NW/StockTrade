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
