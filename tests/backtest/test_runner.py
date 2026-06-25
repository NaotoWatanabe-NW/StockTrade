"""backtest.runner（run_backtest 純粋関数とパラメータ上書き）のテスト

run_backtest はCLIとWeb APIで共有する実行関数。yfinance には触れず、
price_history キャッシュをあらかじめ埋めて（最新足が当日のため再取得が走らない）
オフラインで検証する。
"""

import json
from datetime import date

import pandas as pd
import pytest

import config
from backtest.runner import run_backtest, _apply_param_overrides
from data.db import get_connection
from data.repository import get_backtest_run


def _seed_price_history(conn, code: str, days: int = 300) -> None:
    """当日で終わる連続した日足を price_history に投入する（再取得を避けるため最新足=当日）。"""
    dates = pd.bdate_range(end=date.today(), periods=days)
    rows = []
    for i, d in enumerate(dates):
        # 緩やかな上昇＋オシレーション（指標が計算できる程度の変動を持たせる）
        close = 1000 + i * 0.5 + 20 * (((i % 10) - 5) / 5.0)
        rows.append((code, "1d", str(d.date()), close - 4, close + 8, close - 8, close, 100_000))
    conn.executemany(
        "INSERT OR REPLACE INTO price_history "
        "(code, interval, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()


class TestApplyParamOverrides:
    def test_routes_each_param_to_its_target_cfg(self):
        trade_plan, exit_, backtest, screening, regime = {}, {}, {}, {}, {}
        _apply_param_overrides(
            {"atr_stop_mult": 3.0, "trail_atr_mult": 2.5, "min_abs_score": 40,
             "breakout_lookback": 40, "adx_min": 25},
            {"trade_plan": trade_plan, "exit": exit_, "backtest": backtest,
             "screening": screening, "scoring": {}, "regime": regime},
        )
        assert trade_plan["atr_stop_mult"] == 3.0
        assert exit_["trail_atr_mult"] == 2.5
        assert backtest["min_abs_score"] == 40       # simulator は backtest_cfg から読む
        assert screening["breakout_lookback"] == 40
        assert regime["adx_min"] == 25

    def test_unknown_param_is_rejected(self):
        with pytest.raises(ValueError):
            _apply_param_overrides({"not_a_real_param": 1}, {"trade_plan": {}})

    def test_regime_param_is_ignored_when_regime_disabled(self):
        # regime=None（フィルタ無効）のとき regime 系キーは無視し、例外にしない
        _apply_param_overrides({"adx_min": 25}, {"regime": None})

    def test_none_overrides_is_noop(self):
        cfg = {}
        _apply_param_overrides(None, {"trade_plan": cfg})
        assert cfg == {}


class TestRunBacktestEndToEnd:
    def test_runs_offline_saves_run_and_reflects_overrides(self, monkeypatch):
        conn = get_connection(":memory:")
        monkeypatch.setitem(config.BACKTEST_CONFIG, "history", "1y")
        monkeypatch.setattr(config, "SCREENING_UNIVERSE_JP", ["9999"])
        _seed_price_history(conn, "9999", days=300)

        result = run_backtest(
            universe="JP", regime=False, save=True, conn=conn,
            param_overrides={"min_abs_score": 40, "trail_atr_mult": 3.0},
        )

        assert result["run_id"] is not None
        assert "win_rate" in result["metrics"]

        saved = get_backtest_run(conn, result["run_id"])
        params = json.loads(saved["params"])
        assert params["backtest_cfg"]["min_abs_score"] == 40
        assert params["exit_cfg"]["trail_atr_mult"] == 3.0
        assert params["regime"] is False
        conn.close()

    def test_does_not_mutate_global_config(self, monkeypatch):
        conn = get_connection(":memory:")
        monkeypatch.setitem(config.BACKTEST_CONFIG, "history", "1y")
        monkeypatch.setattr(config, "SCREENING_UNIVERSE_JP", ["9999"])
        _seed_price_history(conn, "9999", days=300)
        before = dict(config.TRADE_PLAN_CONFIG)

        run_backtest(universe="JP", regime=False, save=False, conn=conn,
                     param_overrides={"atr_stop_mult": 99.0})

        assert config.TRADE_PLAN_CONFIG == before  # コピーに対して上書きしている
        conn.close()
