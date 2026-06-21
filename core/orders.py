"""
注文プランの組み立て（SBI証券の注文タイプに変換）

trade_plan が出した価格（エントリー・損切り・利確）を、実際に発注できる
注文タイプに組み立てる。発注自体はAPIが無いため手動だが、通知に
「どの注文を・いくらで置けばよいか」をSBIの用語そのままで提示する。

対応注文タイプ:
  指値 / 逆指値              … 単発のエントリー
  OCO    … 利確の指値 ＋ 損切りの逆指値を同時発注。一方約定で他方失効
  IFD    … 1次注文の約定後に2次（損切り逆指値）が有効化
  IFDOCO … 1次約定後にOCO（利確指値＋損切り逆指値）が有効化

文脈（context）:
  ENTRY … 新規建て・買い増し（→ entry_order_type に従う）
  EXIT  … 保有ロングの手仕舞い（→ exit_order_type に従う）
"""

from dataclasses import dataclass

KIND_LABEL = {"LIMIT": "指値", "STOP": "逆指値"}
SIDE_LABEL = {"BUY": "買い", "SELL": "売り"}


@dataclass(frozen=True)
class Leg:
    """注文の1本（売買方向・指値/逆指値・価格・役割）"""
    side: str    # BUY / SELL
    kind: str    # LIMIT(指値) / STOP(逆指値)
    price: float
    role: str    # "新規買い" / "利確" / "損切り" など

    def text(self, market) -> str:
        return f"{self.role}: {SIDE_LABEL[self.side]}{KIND_LABEL[self.kind]} {market.fmt(self.price)}"


@dataclass(frozen=True)
class OrderPlan:
    """1銘柄に対する注文プラン全体"""
    order_type: str      # 指値 / 逆指値 / OCO / IFD / IFDOCO
    legs: tuple          # tuple[Leg, ...]
    note: str = ""


def _entry_leg(plan: dict) -> Leg:
    side = plan["side"]
    role = "新規買い" if side == "BUY" else "新規売り"
    return Leg(side, plan["entry_kind"], plan["entry"], role)


def build_entry_order(plan: dict, order_type: str) -> OrderPlan:
    """新規建て・買い増しの注文プラン（IFDOCO / IFD / SIMPLE）"""
    entry = _entry_leg(plan)
    exit_side = "SELL" if plan["side"] == "BUY" else "BUY"

    if order_type == "SIMPLE":
        kind_label = KIND_LABEL[plan["entry_kind"]]
        return OrderPlan(kind_label, (entry,), "約定後の決済注文は別途手動で設定")

    if order_type == "IFD":
        stop = Leg(exit_side, "STOP", plan["stop"], "損切り")
        return OrderPlan("IFD", (entry, stop), "1次約定後に損切り逆指値が有効化")

    # IFDOCO（既定）: 1次エントリー → OCO（利確指値 ＋ 損切り逆指値）
    legs = [entry]
    if plan.get("target") is not None:
        legs.append(Leg(exit_side, "LIMIT", plan["target"], "利確"))
    legs.append(Leg(exit_side, "STOP", plan["stop"], "損切り"))
    return OrderPlan("IFDOCO", tuple(legs), "1次約定後にOCO（利確/損切り）が有効化")


def build_exit_order(plan: dict, order_type: str) -> OrderPlan:
    """保有ロング手仕舞いの注文プラン（OCO / STOP）。plan["side"] は SELL 前提"""
    side = plan["side"]  # SELL
    stop = Leg(side, "STOP", plan["stop"], "損切り/撤退")

    if order_type == "STOP":
        return OrderPlan("逆指値", (stop,), "撤退ライン割れで損切り")

    # OCO（既定）: 戻り売り指値（利確） ＋ 撤退逆指値（損切り）
    take = Leg(side, "LIMIT", plan["entry"], "利確/戻り売り")
    return OrderPlan("OCO", (take, stop), "利確と損切りを同時発注、一方約定で他方失効")


def build_order(context: str, plan: dict | None, order_config: dict) -> OrderPlan | None:
    """
    文脈と価格プランから注文プランを組み立てる。

    context: "ENTRY"（新規/買い増し）/ "EXIT"（保有手仕舞い）
    long-only 前提のため、ENTRY×SELL（空売り）と EXIT×BUY は None。
    """
    if plan is None:
        return None
    side = plan["side"]
    if context == "ENTRY" and side == "BUY":
        return build_entry_order(plan, order_config["entry_order_type"])
    if context == "EXIT" and side == "SELL":
        return build_exit_order(plan, order_config["exit_order_type"])
    return None
