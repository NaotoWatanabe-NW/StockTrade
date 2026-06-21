"""
holdings / trades のデータアクセス層（CRUD）

保有銘柄はコード一意。約定履歴は追記のみ（編集は削除→再登録）。
返り値は素の dict で、screener.engine がそのまま扱える形にする。
"""

import sqlite3
from typing import Optional

_HOLDING_FIELDS = ("code", "name", "avg_price", "shares", "market", "long_term")


def _holding_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id":        row["id"],
        "code":      row["code"],
        "name":      row["name"],
        "avg_price": row["avg_price"],
        "shares":    row["shares"],
        "market":    row["market"],
        "long_term": bool(row["long_term"]),
    }


def _trade_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id":        row["id"],
        "code":      row["code"],
        "name":      row["name"],
        "side":      row["side"],
        "shares":    row["shares"],
        "price":     row["price"],
        "fee":       row["fee"],
        "traded_at": row["traded_at"],
        "note":      row["note"],
    }


# ── holdings ────────────────────────────────────────────────

def list_holdings(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM holdings ORDER BY code").fetchall()
    return [_holding_row_to_dict(r) for r in rows]


def get_holding(conn: sqlite3.Connection, code: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM holdings WHERE code = ?", (code,)).fetchone()
    return _holding_row_to_dict(row) if row else None


def upsert_holding(
    conn: sqlite3.Connection,
    code: str,
    name: Optional[str] = None,
    avg_price: Optional[float] = None,
    shares: Optional[float] = None,
    market: Optional[str] = None,
    long_term: bool = False,
) -> dict:
    """コードをキーに挿入、既存なら更新（updated_at を更新）"""
    conn.execute(
        """
        INSERT INTO holdings (code, name, avg_price, shares, market, long_term)
        VALUES (:code, :name, :avg_price, :shares, :market, :long_term)
        ON CONFLICT(code) DO UPDATE SET
            name = excluded.name,
            avg_price = excluded.avg_price,
            shares = excluded.shares,
            market = excluded.market,
            long_term = excluded.long_term,
            updated_at = datetime('now')
        """,
        {
            "code": code, "name": name, "avg_price": avg_price,
            "shares": shares, "market": market, "long_term": int(long_term),
        },
    )
    conn.commit()
    return get_holding(conn, code)


def delete_holding(conn: sqlite3.Connection, code: str) -> bool:
    cur = conn.execute("DELETE FROM holdings WHERE code = ?", (code,))
    conn.commit()
    return cur.rowcount > 0


# ── trades ──────────────────────────────────────────────────

def list_trades(conn: sqlite3.Connection, code: Optional[str] = None) -> list[dict]:
    if code:
        rows = conn.execute(
            "SELECT * FROM trades WHERE code = ? ORDER BY traded_at DESC, id DESC", (code,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY traded_at DESC, id DESC"
        ).fetchall()
    return [_trade_row_to_dict(r) for r in rows]


def add_trade(
    conn: sqlite3.Connection,
    code: str,
    side: str,
    shares: float,
    price: float,
    traded_at: str,
    name: Optional[str] = None,
    fee: float = 0.0,
    note: Optional[str] = None,
) -> dict:
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side は BUY/SELL のいずれか: {side}")
    if shares <= 0 or price < 0:
        raise ValueError("shares は正、price は非負である必要があります")
    cur = conn.execute(
        """
        INSERT INTO trades (code, name, side, shares, price, fee, traded_at, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (code, name, side, shares, price, fee, traded_at, note),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM trades WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _trade_row_to_dict(row)


def delete_trade(conn: sqlite3.Connection, trade_id: int) -> bool:
    cur = conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    conn.commit()
    return cur.rowcount > 0


def realized_pnl(conn: sqlite3.Connection) -> list[dict]:
    """銘柄ごとの実現損益を平均取得単価ベースで集計する。

    実現損益 = 売却額 − (平均取得単価 × 売却株数) − 手数料合計
      平均取得単価 = 買付総額 ÷ 買付株数
    売却がまだ無い銘柄は realized=0（含み損益は監視ツール側で別途算出）。
    残株数 = 買付株数 − 売却株数。
    （ロット単位の厳密な対応＝個別法は将来の強化対象）
    """
    rows = conn.execute(
        """
        SELECT
            code,
            MAX(name) AS name,
            SUM(CASE WHEN side='BUY'  THEN shares ELSE 0 END) AS buy_shares,
            SUM(CASE WHEN side='SELL' THEN shares ELSE 0 END) AS sell_shares,
            SUM(CASE WHEN side='BUY'  THEN price*shares ELSE 0 END) AS buy_amount,
            SUM(CASE WHEN side='SELL' THEN price*shares ELSE 0 END) AS sell_amount,
            SUM(fee) AS fee_total
        FROM trades
        GROUP BY code
        ORDER BY code
        """
    ).fetchall()
    result = []
    for r in rows:
        buy_shares = r["buy_shares"] or 0
        sell_shares = r["sell_shares"] or 0
        buy_amount = r["buy_amount"] or 0
        avg_cost = buy_amount / buy_shares if buy_shares else 0
        realized = (r["sell_amount"] or 0) - avg_cost * sell_shares - (r["fee_total"] or 0)
        result.append({
            "code": r["code"],
            "name": r["name"],
            "buy_shares": buy_shares,
            "sell_shares": sell_shares,
            "remaining_shares": buy_shares - sell_shares,
            "avg_cost": avg_cost,
            "buy_amount": buy_amount,
            "sell_amount": r["sell_amount"] or 0,
            "fee_total": r["fee_total"] or 0,
            "realized": realized,
        })
    return result
