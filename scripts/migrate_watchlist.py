"""
config.py の SCREENING_UNIVERSE を watchlist テーブルへ移行するスクリプト。

べき等（何度実行しても結果が同じ）。既存エントリは上書きせず、
DB にないコードだけ追加する。

実行:
    .venv/bin/python -m scripts.migrate_watchlist
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import SCREENING_UNIVERSE_JP, SCREENING_UNIVERSE_US
from data.db import get_connection
from data.repository import get_watchlist_item, upsert_watchlist

MARKET_JP = {code: "JP" for code in SCREENING_UNIVERSE_JP}
MARKET_US = {code: "US" for code in SCREENING_UNIVERSE_US}
ALL = {**MARKET_JP, **MARKET_US}


def main():
    conn = get_connection()
    added = 0
    skipped = 0
    for code, market in ALL.items():
        if get_watchlist_item(conn, code):
            skipped += 1
        else:
            upsert_watchlist(conn, code=code, market=market)
            added += 1

    conn.close()
    print(f"完了: 追加 {added} 件 / スキップ（既存） {skipped} 件")
    print(f"合計 {added + skipped} 銘柄がウォッチリストに登録されています。")


if __name__ == "__main__":
    main()
