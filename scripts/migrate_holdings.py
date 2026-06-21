"""
holdings_local.py（旧形式）→ SQLite への移行スクリプト

1回だけ実行して、py ファイルに書いていた保有銘柄をDBへ取り込む。
以後の編集はWebアプリ／DBで行う。べき等（再実行しても重複しない＝upsert）。

  $ .venv/bin/python -m scripts.migrate_holdings
"""

import sys

from data.db import get_connection
from data.repository import upsert_holding, list_holdings


def main() -> int:
    try:
        from holdings_local import HOLDINGS  # type: ignore
    except ImportError:
        print("holdings_local.py が見つかりません。移行対象がないため終了します。")
        return 0

    conn = get_connection()
    for h in HOLDINGS:
        upsert_holding(
            conn,
            code=str(h["code"]),
            name=h.get("name"),
            avg_price=h.get("avg_price"),
            shares=h.get("shares"),
            market=h.get("market"),
            long_term=bool(h.get("long_term", False)),
        )
    count = len(list_holdings(conn))
    conn.close()
    print(f"✅ 移行完了: holdings テーブルに {count} 銘柄を登録しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
