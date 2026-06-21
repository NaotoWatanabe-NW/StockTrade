"""
市場（日本株 / 米国株）の定義と判定

銘柄コードから所属市場を推定し、yfinanceティッカー形式・通貨表記・
取引時間・タイムゾーンをまとめて扱う。

判定ルール:
  - 末尾が ".T"           → 東証
  - 4桁などの数字のみ      → 東証（例: 7203）
  - アルファベットを含む   → 米国（例: AAPL, BRK-B）
  - config側で market="JP"/"US" を明示した場合はそれを優先
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Market:
    code: str       # "JP" / "US"
    name: str       # 表示名
    suffix: str     # yfinanceティッカーの接尾辞（".T" / ""）
    currency: str   # 通貨記号
    tz: str         # タイムゾーン（zoneinfo名）
    open: str       # 取引開始 "HH:MM"（現地時間）
    close: str      # 取引終了 "HH:MM"（現地時間）

    def ticker(self, code: str) -> str:
        """銘柄コード → yfinanceティッカー形式"""
        c = str(code).strip().upper()
        if self.suffix and not c.endswith(self.suffix):
            return c + self.suffix
        return c

    def fmt(self, value) -> str:
        """価格を市場通貨で整形（円は整数、ドルは小数2桁）"""
        if value is None:
            return "-"
        if self.currency == "¥":
            return f"¥{value:,.0f}"
        return f"{self.currency}{value:,.2f}"


JP = Market("JP", "東証", ".T", "¥", "Asia/Tokyo", "09:00", "15:30")
US = Market("US", "米国", "", "$", "America/New_York", "09:30", "16:00")

_BY_CODE = {"JP": JP, "US": US}


def resolve_market(code: str, explicit: str | None = None) -> Market:
    """銘柄コード（と任意の明示指定）から所属市場を判定"""
    if explicit:
        key = explicit.strip().upper()
        if key not in _BY_CODE:
            raise ValueError(f"未知の市場指定: {explicit}（JP / US のみ対応）")
        return _BY_CODE[key]

    c = str(code).strip().upper()
    if c.endswith(".T"):
        return JP
    # 数字のみ（東証の証券コード）。ハイフン等を含む英字は米国ティッカー
    if c.replace(".", "").isdigit():
        return JP
    return US
