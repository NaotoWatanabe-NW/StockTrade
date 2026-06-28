"""
銘柄→業種の分類を取得して sectors テーブルへ同期するスクリプト。

  - 日本株: J-Quants 無料版 /listed/info から 17/33業種を取得（要 .env 資格情報）。
            分類はほぼ静的なので12週遅延の影響を受けない。
  - 米国株: yfinance の .info["sector"] から取得（スクリーニングユニバース分のみ）。

業種スコア（合議スコアの sector 成分）が参照する分類のキャッシュを作る。
分類はめったに変わらないので、月1回程度の実行で十分。

実行:
    .venv/bin/python -m scripts.sync_sectors                 # JP+US を同期
    .venv/bin/python -m scripts.sync_sectors --market JP     # 日本株のみ
    .venv/bin/python -m scripts.sync_sectors --dry-run       # 書き込まず件数だけ表示
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from core.market import resolve_market
from data.db import get_connection
from data.repository import upsert_sectors


def _collect_jp(dry_run: bool) -> list[dict]:
    """J-Quants 無料版から日本株の業種分類を取得する。資格情報が無ければ空。"""
    from data.jquants import fetch_listed_sectors, JQuantsError
    try:
        rows = fetch_listed_sectors()
    except JQuantsError as e:
        print(f"⚠️ J-Quants から取得できませんでした（日本株はスキップ）: {e}")
        return []
    print(f"  J-Quants: {len(rows)} 銘柄の業種分類を取得")
    return rows


def _collect_us(codes: list[str], dry_run: bool) -> list[dict]:
    """yfinance からユニバースの米国株の sector を取得する。"""
    from core.data_client import StockDataClient
    client = StockDataClient(rate_limit_sec=1.0)
    rows = []
    for code in codes:
        market = resolve_market(code)
        if market.code != "US":
            continue
        info = client.get_info(code, market)
        sector = (info or {}).get("sector") or None
        if sector in (None, "-", ""):
            print(f"  - {code}: sector 不明（スキップ）")
            continue
        rows.append({
            "code":          code,
            "name":          (info or {}).get("name"),
            "sector33_name": sector,
            "sector_group":  sector,
            "market_code":   "US",
        })
        print(f"  - {code}: {sector}")
    return rows


def main():
    parser = argparse.ArgumentParser(description="業種分類を sectors テーブルへ同期")
    parser.add_argument("--market", choices=["JP", "US", "ALL"], default="ALL")
    parser.add_argument("--dry-run", action="store_true",
                        help="DB に書き込まず取得件数だけ表示")
    args = parser.parse_args()

    rows: list[dict] = []
    if args.market in ("JP", "ALL"):
        print("日本株（J-Quants）の業種を取得中...")
        rows.extend(_collect_jp(args.dry_run))
    if args.market in ("US", "ALL"):
        print("米国株（yfinance）の業種を取得中...")
        rows.extend(_collect_us(config.SCREENING_UNIVERSE_US, args.dry_run))

    if args.dry_run:
        groups = sorted({r.get("sector_group") for r in rows if r.get("sector_group")})
        print(f"\n[dry-run] 取得 {len(rows)} 件 / 業種グループ {len(groups)} 種")
        print("業種グループ:", ", ".join(groups[:40]) + (" ..." if len(groups) > 40 else ""))
        return

    if not rows:
        print("同期対象がありませんでした。")
        return

    conn = get_connection()
    try:
        n = upsert_sectors(conn, rows)
    finally:
        conn.close()
    print(f"\n完了: {n} 件の業種分類を sectors テーブルへ保存しました。")


if __name__ == "__main__":
    main()
