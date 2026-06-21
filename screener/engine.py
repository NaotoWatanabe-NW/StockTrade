"""
銘柄スクリーニングエンジン

ユニバース（監視対象銘柄群）に対してテクニカル条件をスキャンし、
シグナルが出た銘柄に「注文プラン（指値・損切り・利確）」を付けて抽出する。
保有銘柄については含み損益（金額・率）も計算する。
日本株・米国株の両方に対応（各銘柄の市場は自動判定）。
"""

import logging
from typing import Optional

import pandas as pd

from core.data_client import StockDataClient
from core.events import should_avoid_earnings
from core.market import Market, resolve_market
from core.orders import build_order
from core.scoring import DEFAULT_SCORING_CONFIG
from core.strategy import evaluate

log = logging.getLogger(__name__)


class StockScreener:
    def __init__(self, client: StockDataClient, config: dict,
                 trade_plan_config: dict, order_config: dict,
                 scoring_config: dict | None = None,
                 risk_config: dict | None = None,
                 regime_config: dict | None = None,
                 events_config: dict | None = None):
        self.client = client
        self.config = config
        self.trade_plan_config = trade_plan_config
        self.order_config = order_config
        self.scoring_config = scoring_config or DEFAULT_SCORING_CONFIG
        self.risk_config = risk_config      # None の場合はサイジングを行わない
        self.regime_config = regime_config  # None の場合はレジームフィルタなし
        self.events_config = events_config  # None の場合は決算回避なし

    def _fetch_index(self, market_code: str) -> Optional[pd.DataFrame]:
        """指数日足データを取得（scan_universe で1回だけ呼ぶ）"""
        if not self.regime_config:
            return None
        cfg = self.regime_config
        code = cfg.get("jp_index", "^N225") if market_code == "JP" else cfg.get("us_index", "^GSPC")
        try:
            import yfinance as yf
            df = yf.Ticker(code).history(period="3y", interval="1d")
            if df.empty:
                return None
            df.columns = [c.lower() for c in df.columns]
            return df
        except Exception as e:
            log.warning(f"指数データ取得失敗 {code}: {e}")
            return None

    def _decide(self, code: str, market: Market,
                df_weekly: Optional[pd.DataFrame] = None,
                df_index: Optional[pd.DataFrame] = None):
        """1銘柄を取得して strategy.evaluate() に委譲する"""
        df = self.client.get_history(code, market, period="6mo", interval="1d")
        return evaluate(
            df, self.config, self.scoring_config, self.trade_plan_config,
            risk_cfg=self.risk_config, market_code=market.code,
            regime_cfg=self.regime_config,
            df_weekly=df_weekly,
            df_index=df_index,
        )

    def _order(self, context: str, plan):
        return build_order(context, plan, self.order_config)

    def scan_universe(self, codes: list[str]) -> list[dict]:
        """
        ユニバース全体をスキャンし、シグナルが出た銘柄をリストアップ。

        戻り値: [{"code","name","market","price","change_pct","signals","trade_plan"}]
        """
        results = []

        # 指数データはスキャン開始時に1回取得（JP / US 各1回）
        index_cache: dict[str, Optional[pd.DataFrame]] = {}

        for code in codes:
            market = resolve_market(code)

            # 指数データキャッシュ
            if market.code not in index_cache:
                index_cache[market.code] = self._fetch_index(market.code)
            df_index = index_cache[market.code]

            # 週足データ（レジームフィルタ有効時のみ取得）
            df_weekly = None
            if self.regime_config and self.regime_config.get("weekly_trend_filter", True):
                df_weekly = self.client.get_history(code, market, period="2y", interval="1wk")

            d = self._decide(code, market, df_weekly=df_weekly, df_index=df_index)
            if d is None:
                continue

            # 流動性・最低株価フィルター（ボロ株・低流動性を除外）
            if d.price < self.config["min_price"]:
                continue
            if d.avg_volume < self.config["min_avg_volume"]:
                continue
            if not d.signals:
                continue

            # レジームフィルタ
            if not d.filters.get("passed", True):
                log.info(f"  ✗ [{market.code}] {code}: レジームフィルタ除外 {d.filters}")
                continue

            # 合議スコアの確度フィルタ（min_abs_score=0なら無効）
            min_abs = self.scoring_config.get("min_abs_score", 0)
            if d.consensus and min_abs > 0 and abs(d.consensus.score) < min_abs:
                continue

            # 決算回避フィルタ（events_config が有効で、近日中に決算がある銘柄を除外）
            if self.events_config:
                t_date = pd.Timestamp(d.price).date() if False else __import__('datetime').date.today()
                if should_avoid_earnings(code, market.code, t_date, self.events_config):
                    log.info(f"  ✗ [{market.code}] {code}: 決算回避フィルタ除外")
                    continue

            info = self.client.get_info(code, market)
            name = info["name"] if info else code

            results.append({
                "code":             code,
                "name":             name,
                "market":           market,
                "price":            d.price,
                "change_pct":       d.change_pct,
                "signals":          d.signals,
                "score":            d.consensus,
                "trade_plan":       d.trade_plan,
                "suggested_shares": d.shares,
                "filters":          d.filters,
                "order":            self._order("ENTRY", d.trade_plan),
            })

            score_txt = f"{d.consensus.score:+.0f}" if d.consensus else "-"
            log.info(f"  ✓ [{market.code}] {name}（{code}）: "
                     f"score {score_txt} / {[s['type'] for s in d.signals]}")

        # 確度の高い順（スコア降順）に並べる
        results.sort(key=lambda r: r["score"].score if r["score"] else 0, reverse=True)
        return results

    def check_holdings(self, holdings: list[dict]) -> list[dict]:
        """
        保有銘柄に対してシグナルチェック＋含み損益計算。
        （ユニバーススクリーニングと違い、全保有銘柄を必ずチェック）
        レジーム/決算フィルタは保有銘柄には適用しない（既存ポジションの管理のため）。

        holdings 各要素: {"code","name","avg_price","shares"?, "market"?}
        """
        results = []

        for h in holdings:
            code = h["code"]
            market = resolve_market(code, h.get("market"))
            # 保有銘柄はレジームフィルタを通さない
            d = self._decide(code, market)
            if d is None:
                continue

            avg_price = h.get("avg_price")
            shares = h.get("shares")

            unrealized_pct = None
            unrealized_amount = None
            if avg_price:
                unrealized_pct = (d.price - avg_price) / avg_price * 100
                if shares:
                    unrealized_amount = (d.price - avg_price) * shares

            # 保有銘柄: 売りシグナルなら手仕舞い(EXIT)、買いシグナルなら買い増し(ENTRY)
            context = "EXIT" if (d.trade_plan and d.trade_plan["side"] == "SELL") else "ENTRY"

            results.append({
                "code":              code,
                "name":              h.get("name", code),
                "market":            market,
                "long_term":         bool(h.get("long_term", False)),
                "price":             d.price,
                "change_pct":        d.change_pct,
                "avg_price":         avg_price,
                "shares":            shares,
                "unrealized_pct":    unrealized_pct,
                "unrealized_amount": unrealized_amount,
                "signals":           d.signals,
                "score":             d.consensus,
                "trade_plan":        d.trade_plan,
                "suggested_shares":  d.shares,
                "order":             self._order(context, d.trade_plan),
            })

            if d.signals:
                log.info(f"  ⚡ [保有/{market.code}] {h.get('name', code)}（{code}）: "
                         f"{[s['type'] for s in d.signals]}")

        return results
