"""銘柄コード → 銘柄名の解決API（フォームのオートフィル用）"""

from typing import Optional

from fastapi import APIRouter, Depends

from api.deps import get_db
from api.schemas import NameLookup
from data.repository import find_known_name

router = APIRouter(prefix="/api/lookup", tags=["lookup"])


@router.get("/name", response_model=NameLookup)
def lookup_name(code: str, market: Optional[str] = None, conn=Depends(get_db)):
    """コードから銘柄名を返す。まず DB の既知名、無ければ yfinance にフォールバック。

    source: "db"（登録済み）/ "yfinance"（外部取得）/ None（不明）。
    """
    code = code.strip()
    if not code:
        return NameLookup(code=code, name=None, source=None)

    known = find_known_name(conn, code)
    if known:
        return NameLookup(code=code, name=known, source="db")

    # yfinance フォールバック（ネットワーク。失敗しても name=None で返す）
    try:
        from core.market import resolve_market
        from core.data_client import StockDataClient
        mkt = resolve_market(code, market)
        info = StockDataClient(rate_limit_sec=0.0).get_info(code, mkt)
        if info and info.get("name") and info["name"] != code:
            return NameLookup(code=code, name=info["name"], source="yfinance")
    except Exception:
        pass

    return NameLookup(code=code, name=None, source=None)
