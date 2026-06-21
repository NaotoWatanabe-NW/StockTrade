"""
銘柄スクリーニングエンジン

ユニバース（監視対象銘柄群）に対してテクニカル条件をスキャンし、
シグナルが出た銘柄に「注文プラン（指値・損切り・利確）」を付けて抽出する。
保有銘柄については含み損益（金額・率）も計算する。
日本株・米国株の両方に対応（各銘柄の市場は自動判定）。
"""

import logging
from typing import Optional

from core.data_client import StockDataClient
from core.indicators import add_technical_indicators, detect_signals
from core.market import Market, resolve_market
from core.trade_plan import net_side, entry_style, build_trade_plan
from core.orders import build_order
from core.scoring import compute_consensus, DEFAULT_SCORING_CONFIG

log = logging.getLogger(__name__)


class StockScreener:
    def __init__(self, client: StockDataClient, config: dict,
                 trade_plan_config: dict, order_config: dict,
                 scoring_config: dict | None = None):
        self.client = client
        self.config = config
        self.trade_plan_config = trade_plan_config
        self.order_config = order_config
        self.scoring_config = scoring_config or DEFAULT_SCORING_CONFIG

    def _analyze(self, code: str, market: Market) -> Optional[dict]:
        """1銘柄を取得・指標計算・シグナル判定し、共通の解析結果を返す"""
        df = self.client.get_history(code, market, period="6mo", interval="1d")
        if df is None or len(df) < self.config["ma_long"] + 5:
            return None

        df = add_technical_indicators(df, self.config)
        signals = detect_signals(df, self.config)

        latest = df.iloc[-1]
        prev_close = df.iloc[-2]["close"] if len(df) >= 2 else latest["close"]
        change_pct = (latest["close"] - prev_close) / prev_close * 100
        atr = float(latest["atr"]) if latest["atr"] == latest["atr"] else None  # NaN除外

        return {
            "df": df,
            "price": float(latest["close"]),
            "avg_volume": float(df["volume"].tail(20).mean()),
            "change_pct": float(change_pct),
            "atr": atr,
            "signals": signals,
            "consensus": compute_consensus(df, self.scoring_config),
        }

    def _trade_plan(self, signals: list[dict], price: float, atr: Optional[float]) -> Optional[dict]:
        side = net_side(signals)
        style = entry_style(signals)
        return build_trade_plan(side, price, atr, self.trade_plan_config, style)

    def _order(self, context: str, plan: Optional[dict]):
        return build_order(context, plan, self.order_config)

    def scan_universe(self, codes: list[str]) -> list[dict]:
        """
        ユニバース全体をスキャンし、シグナルが出た銘柄をリストアップ。

        戻り値: [{"code","name","market","price","change_pct","signals","trade_plan"}]
        """
        results = []

        for code in codes:
            market = resolve_market(code)
            a = self._analyze(code, market)
            if a is None:
                continue

            # 流動性・最低株価フィルター（ボロ株・低流動性を除外）
            if a["price"] < self.config["min_price"]:
                continue
            if a["avg_volume"] < self.config["min_avg_volume"]:
                continue
            if not a["signals"]:
                continue

            info = self.client.get_info(code, market)
            name = info["name"] if info else code

            consensus = a["consensus"]
            # 合議スコアの確度フィルタ（min_abs_score=0なら無効）
            min_abs = self.scoring_config.get("min_abs_score", 0)
            if consensus and min_abs > 0 and abs(consensus.score) < min_abs:
                continue

            plan = self._trade_plan(a["signals"], a["price"], a["atr"])
            results.append({
                "code":       code,
                "name":       name,
                "market":     market,
                "price":      a["price"],
                "change_pct": a["change_pct"],
                "signals":    a["signals"],
                "score":      consensus,
                "trade_plan": plan,
                "order":      self._order("ENTRY", plan),  # 新規買い候補
            })

            score_txt = f"{consensus.score:+.0f}" if consensus else "-"
            log.info(f"  ✓ [{market.code}] {name}（{code}）: "
                     f"score {score_txt} / {[s['type'] for s in a['signals']]}")

        # 確度の高い順（スコア降順）に並べる
        results.sort(key=lambda r: r["score"].score if r["score"] else 0, reverse=True)
        return results

    def check_holdings(self, holdings: list[dict]) -> list[dict]:
        """
        保有銘柄に対してシグナルチェック＋含み損益計算。
        （ユニバーススクリーニングと違い、全保有銘柄を必ずチェック）

        holdings 各要素: {"code","name","avg_price","shares"?, "market"?}
        """
        results = []

        for h in holdings:
            code = h["code"]
            market = resolve_market(code, h.get("market"))
            a = self._analyze(code, market)
            if a is None:
                continue

            price = a["price"]
            avg_price = h.get("avg_price")
            shares = h.get("shares")

            unrealized_pct = None
            unrealized_amount = None
            if avg_price:
                unrealized_pct = (price - avg_price) / avg_price * 100
                if shares:
                    unrealized_amount = (price - avg_price) * shares

            plan = self._trade_plan(a["signals"], price, a["atr"])
            # 保有銘柄: 売りシグナルなら手仕舞い(EXIT)、買いシグナルなら買い増し(ENTRY)
            context = "EXIT" if (plan and plan["side"] == "SELL") else "ENTRY"

            results.append({
                "code":              code,
                "name":              h.get("name", code),
                "market":            market,
                "long_term":         bool(h.get("long_term", False)),
                "price":             price,
                "change_pct":        a["change_pct"],
                "avg_price":         avg_price,
                "shares":            shares,
                "unrealized_pct":    unrealized_pct,
                "unrealized_amount": unrealized_amount,
                "signals":           a["signals"],
                "score":             a["consensus"],
                "trade_plan":        plan,
                "order":             self._order(context, plan),
            })

            if a["signals"]:
                log.info(f"  ⚡ [保有/{market.code}] {h.get('name', code)}（{code}）: "
                         f"{[s['type'] for s in a['signals']]}")

        return results
