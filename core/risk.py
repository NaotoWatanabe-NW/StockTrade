"""
ポジションサイジング（Phase 2）

固定リスク%方式: 口座サイズ × リスク% を 1 トレードで失ってよい最大額とし、
そこから損切り幅（entry_price − stop_price）を割って株数を求める。

  max_risk_amount = account_size × risk_per_trade_pct / 100
  shares_raw      = max_risk_amount / risk_per_share
  shares          = floor(shares_raw / lot_size) × lot_size

日本株は lot_size=100 が一般的。米国株は lot_size=1。
lot_size は RISK_CONFIG で指定するか、market から自動判定できる。

口座全体のヒート（同時保有リスクの合計）が max_positions × risk_per_trade_pct
を超えないよう、同時保有上限も提供する。
"""

from __future__ import annotations

from typing import Optional


def calc_shares(
    account_size: float,
    risk_per_trade_pct: float,
    entry_price: float,
    stop_price: float,
    lot_size: int = 1,
) -> int:
    """
    1 トレード分の推奨株数を返す。

    account_size       : 口座残高（円 or ドル）
    risk_per_trade_pct : 1 トレード許容リスク（口座の%。例: 1.0 = 1%）
    entry_price        : エントリー価格
    stop_price         : 損切り価格
    lot_size           : 売買単位（JP は通常 100、US は 1）

    戻り値: 株数（lot_size の倍数）。計算不能なら 0。
    """
    if account_size <= 0 or entry_price <= 0:
        return 0
    risk_per_share = abs(entry_price - stop_price)
    if risk_per_share <= 0:
        return 0
    max_risk = account_size * risk_per_trade_pct / 100.0
    shares_raw = max_risk / risk_per_share
    shares = int(shares_raw // lot_size) * lot_size
    return max(0, shares)


def lot_size_for_market(market_code: str) -> int:
    """市場コードから標準売買単位を返す（JP:100, US:1）。"""
    return 100 if market_code == "JP" else 1


def calc_position_value(shares: int, entry_price: float) -> float:
    """投資金額（= 株数 × エントリー価格）を返す。"""
    return shares * entry_price


def heat(
    account_size: float,
    risk_per_trade_pct: float,
    open_positions: int,
) -> float:
    """
    現在のポートフォリオヒート（口座に対する総リスク割合）を返す。

    open_positions : 現在の保有ポジション数
    """
    return risk_per_trade_pct * open_positions
