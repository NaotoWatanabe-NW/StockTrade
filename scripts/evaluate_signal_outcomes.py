"""過去シグナルの「予測 vs 実勢価格」を評価して signal_outcomes に記録するツール。

signals テーブルに記録した計画（entry/stop/target）が、生成後の実勢価格で
どう決着したか（利確到達／損切到達／期間内未決着／未約定）を判定して保存する。
実際に取引したかには依存せず、シグナル自体の的中度を測るための土台。
これにより score → 勝率/期待R のキャリブレーション（screener.signal_outcome 参照）が
データで確認できるようになる。

評価対象は BUY シグナルのみ（SELL は手仕舞い指示で入口予測ではない）。
確定済み（NO_ENTRY/TARGET/STOP/TIMEOUT）は再評価せず、PENDING と未評価のみ処理する。

実行:
    .venv/bin/python -m scripts.evaluate_signal_outcomes            # 評価して保存
    .venv/bin/python -m scripts.evaluate_signal_outcomes --dry-run  # 保存せず結果表示
"""

import sys
import argparse
import logging
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

warnings.filterwarnings("ignore")

import pandas as pd

from config import BACKTEST_CONFIG
from core.market import resolve_market
from data.db import get_connection
from data.price_cache import get_history_cached
from data.repository import save_signal_outcome, signals_needing_outcome_eval
from screener.signal_outcome import evaluate_signal_outcome, PENDING

log = logging.getLogger(__name__)


def _bars_after(df: pd.DataFrame, generated_at: str) -> pd.DataFrame:
    """生成日より後（翌足以降）の日足だけにスライスする。ルックアヘッド回避。"""
    gen_date = pd.Timestamp(str(generated_at)[:10])
    idx = df.index
    naive = idx.tz_localize(None) if getattr(idx, "tz", None) is not None else idx
    return df[naive.normalize() > gen_date]


def evaluate_all(dry_run: bool = False) -> list[dict]:
    """評価が必要なシグナルを順に評価し、結果（表示用 dict）のリストを返す。"""
    horizon = int(BACKTEST_CONFIG.get("max_hold_bars", 20))
    entry_valid = int(BACKTEST_CONFIG.get("entry_order_valid_days", 15))

    conn = get_connection()
    results: list[dict] = []
    try:
        pending = signals_needing_outcome_eval(conn)
        log.info("評価対象シグナル: %d 件", len(pending))

        for sig in pending:
            market = resolve_market(sig["code"], sig.get("market"))
            df = get_history_cached(conn, sig["code"], market, interval="1d", years=2)
            if df is None or df.empty:
                log.warning("価格データなしでスキップ: %s", sig["code"])
                continue

            df_after = _bars_after(df, sig["generated_at"])
            outcome = evaluate_signal_outcome(sig, df_after, horizon, entry_valid)
            if outcome is None:
                continue  # 評価対象外（BUY 以外 / R 未定義）

            if not dry_run:
                save_signal_outcome(conn, sig["id"], outcome)

            results.append({"signal": sig, "outcome": outcome})
    finally:
        conn.close()

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="シグナルの予測結果を評価して記録")
    parser.add_argument("--dry-run", action="store_true",
                        help="DBに保存せず評価結果を表示する")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    results = evaluate_all(dry_run=args.dry_run)
    if not results:
        print("評価対象のシグナルはありませんでした。")
        return 0

    resolved = [r for r in results if r["outcome"]["outcome"] != PENDING]
    print(f"評価: {len(results)} 件（うち確定 {len(resolved)} 件）"
          + ("  ※--dry-run のため未保存" if args.dry_run else ""))
    for r in results:
        s, o = r["signal"], r["outcome"]
        rr = o["realized_r"]
        rr_s = f"{rr:+.2f}R" if rr is not None else "—"
        print(f"  {s['code']:6} score={s.get('score') or 0:+5.0f}  "
              f"{o['outcome']:8}  R={rr_s}  (eval〜{o.get('eval_through')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
