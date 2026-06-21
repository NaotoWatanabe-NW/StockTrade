"""
注文プランの組み立て（SBI証券の注文タイプに変換）

trade_plan が出した価格（エントリー・損切り・利確）を、実際に発注できる
注文タイプに組み立てる。発注自体はAPIが無いため手動だが、通知に
「どの注文を・いくらで置けばよいか」をSBIの用語そのままで提示する。

対応注文タイプ（日本株）:
  指値 / 逆指値              … 単発のエントリー
  OCO    … 利確の指値 ＋ 損切りの逆指値を同時発注。一方約定で他方失効
  IFD    … 1次注文の約定後に2次（損切り逆指値）が有効化
  IFDOCO … 1次約定後にOCO（利確指値＋損切り逆指値）が有効化

⚠️ 米国株（SBI）は OCO / IFD / IFDOCO が使えない。
   このためエントリーは単発の指値/逆指値とし、損切り・利確は
   「約定後に手動で置く参考注文（followups）」として併記する。

執行条件（exec_cond）:
  指値（ザラ場で待つ注文）   → 「条件なし」（その日のザラ場中ずっと有効）
  逆指値（トリガー後に発注）  → 「成行」（確実に約定。損切り・ブレイク追随を優先）

文脈（context）:
  ENTRY … 新規建て・買い増し（→ entry_order_type に従う）
  EXIT  … 保有ロングの手仕舞い（→ exit_order_type に従う）
"""

from dataclasses import dataclass

KIND_LABEL = {"LIMIT": "指値", "STOP": "逆指値"}
SIDE_LABEL = {"BUY": "買い", "SELL": "売り"}

# 執行条件: 指値はザラ場中ずっと有効（条件なし）、逆指値はトリガー後に成行で確実に約定。
EXEC_COND = {"LIMIT": "条件なし", "STOP": "成行"}


def _exec_cond(kind: str) -> str:
    """注文種別（指値/逆指値）に応じた既定の執行条件を返す。"""
    return EXEC_COND.get(kind, "")


@dataclass(frozen=True)
class Leg:
    """注文の1本（売買方向・指値/逆指値・価格・役割・執行条件）"""
    side: str          # BUY / SELL
    kind: str          # LIMIT(指値) / STOP(逆指値)
    price: float
    role: str          # "新規買い" / "利確" / "損切り" など
    exec_cond: str = ""  # "条件なし" / "成行" など（SBIの執行条件。空なら非表示）

    def text(self, market) -> str:
        cond = f"（{self.exec_cond}）" if self.exec_cond else ""
        return (
            f"{self.role}: {SIDE_LABEL[self.side]}{KIND_LABEL[self.kind]} "
            f"{market.fmt(self.price)}{cond}"
        )


@dataclass(frozen=True)
class OrderPlan:
    """1銘柄に対する注文プラン全体"""
    order_type: str          # 指値 / 逆指値 / OCO / IFD / IFDOCO
    legs: tuple              # tuple[Leg, ...] いま発注するレッグ
    note: str = ""
    followups: tuple = ()    # 米国株: 約定後に手動で置く参考注文（損切り/利確）


def _entry_leg(plan: dict) -> Leg:
    side = plan["side"]
    role = "新規買い" if side == "BUY" else "新規売り"
    kind = plan["entry_kind"]
    return Leg(side, kind, plan["entry"], role, _exec_cond(kind))


def build_entry_order(plan: dict, order_type: str, is_us: bool = False) -> OrderPlan:
    """新規建て・買い増しの注文プラン（IFDOCO / IFD / SIMPLE、米国株は単発＋参考）"""
    entry = _entry_leg(plan)
    exit_side = "SELL" if plan["side"] == "BUY" else "BUY"
    take = (
        Leg(exit_side, "LIMIT", plan["target"], "利確", _exec_cond("LIMIT"))
        if plan.get("target") is not None else None
    )
    stop = Leg(exit_side, "STOP", plan["stop"], "損切り", _exec_cond("STOP"))

    if is_us:
        # 米国株は OCO/IFD/IFDOCO 不可 → 単発エントリー＋約定後に手動設定する参考注文
        followups = tuple(leg for leg in (take, stop) if leg is not None)
        return OrderPlan(
            KIND_LABEL[plan["entry_kind"]], (entry,),
            "米国株はOCO/IFD/IFDOCO不可。約定後に下記を手動で設定",
            followups=followups,
        )

    if order_type == "SIMPLE":
        return OrderPlan(KIND_LABEL[plan["entry_kind"]], (entry,),
                         "約定後の決済注文は別途手動で設定")

    if order_type == "IFD":
        return OrderPlan("IFD", (entry, stop), "1次約定後に損切り逆指値が有効化")

    # IFDOCO（既定）: 1次エントリー → OCO（利確指値 ＋ 損切り逆指値）
    legs = [entry]
    if take is not None:
        legs.append(take)
    legs.append(stop)
    return OrderPlan("IFDOCO", tuple(legs), "1次約定後にOCO（利確/損切り）が有効化")


def build_exit_order(plan: dict, order_type: str, is_us: bool = False) -> OrderPlan:
    """保有ロング手仕舞いの注文プラン（OCO / STOP、米国株は逆指値＋参考）。
    plan["side"] は SELL 前提。"""
    side = plan["side"]  # SELL
    stop = Leg(side, "STOP", plan["stop"], "損切り/撤退", _exec_cond("STOP"))
    take = Leg(side, "LIMIT", plan["entry"], "利確/戻り売り", _exec_cond("LIMIT"))

    if is_us:
        # 米国株は OCO 不可 → 撤退の逆指値を主注文、利確指値は参考（別途手動）
        return OrderPlan(
            "逆指値", (stop,),
            "米国株はOCO不可。利確は別途手動で指値設定（約定したら損切りを取消）",
            followups=(take,),
        )

    if order_type == "STOP":
        return OrderPlan("逆指値", (stop,), "撤退ライン割れで損切り")

    # OCO（既定）: 戻り売り指値（利確） ＋ 撤退逆指値（損切り）
    return OrderPlan("OCO", (take, stop), "利確と損切りを同時発注、一方約定で他方失効")


def build_order(context: str, plan: dict | None, order_config: dict,
                market=None) -> OrderPlan | None:
    """
    文脈と価格プランから注文プランを組み立てる。

    context: "ENTRY"（新規/買い増し）/ "EXIT"（保有手仕舞い）
    market : Market（code が "US" なら OCO/IFD/IFDOCO 不可として単発＋参考に切替）
             None の場合は日本株扱い（従来どおりの組合せ注文）。
    long-only 前提のため、ENTRY×SELL（空売り）と EXIT×BUY は None。
    """
    if plan is None:
        return None
    is_us = getattr(market, "code", None) == "US"
    side = plan["side"]
    if context == "ENTRY" and side == "BUY":
        return build_entry_order(plan, order_config["entry_order_type"], is_us=is_us)
    if context == "EXIT" and side == "SELL":
        return build_exit_order(plan, order_config["exit_order_type"], is_us=is_us)
    return None
