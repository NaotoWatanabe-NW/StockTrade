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
    keys = row.keys()
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
        "signal_id": row["signal_id"] if "signal_id" in keys else None,
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

def get_trade(conn: sqlite3.Connection, trade_id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    return _trade_row_to_dict(row) if row else None


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
    signal_id: Optional[int] = None,
) -> dict:
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side は BUY/SELL のいずれか: {side}")
    if shares <= 0 or price < 0:
        raise ValueError("shares は正、price は非負である必要があります")
    cur = conn.execute(
        """
        INSERT INTO trades (code, name, side, shares, price, fee, traded_at, note, signal_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (code, name, side, shares, price, fee, traded_at, note, signal_id),
    )
    conn.commit()
    # シグナルに紐付いた場合は状態と実現Rを再計算
    if signal_id is not None:
        _on_signal_trade_change(conn, signal_id)
    row = conn.execute("SELECT * FROM trades WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _trade_row_to_dict(row)


def delete_trade(conn: sqlite3.Connection, trade_id: int) -> bool:
    # 削除前に紐付くシグナルを控えておき、削除後に状態を再計算する
    row = conn.execute("SELECT signal_id FROM trades WHERE id = ?", (trade_id,)).fetchone()
    signal_id = row["signal_id"] if row else None
    cur = conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    conn.commit()
    if signal_id is not None:
        _on_signal_trade_change(conn, signal_id)
    return cur.rowcount > 0


def sync_holding_from_trades(conn: sqlite3.Connection, code: str) -> None:
    """約定履歴から保有の建値・株数を再計算して更新する。

    - 全 BUY 約定の加重平均を avg_price とする
    - 残株数 = BUY 合計 - SELL 合計
    - 保有テーブルに銘柄が存在しない場合はスキップ
    - BUY 約定が1件もない場合は avg_price を変更せず株数だけ更新
    """
    if get_holding(conn, code) is None:
        return

    rows = conn.execute(
        "SELECT side, shares, price FROM trades WHERE code = ?", (code,)
    ).fetchall()

    if not rows:
        return

    buy_shares = sum(r["shares"] for r in rows if r["side"] == "BUY")
    sell_shares = sum(r["shares"] for r in rows if r["side"] == "SELL")
    buy_amount = sum(r["shares"] * r["price"] for r in rows if r["side"] == "BUY")

    avg_price = buy_amount / buy_shares if buy_shares > 0 else None
    remaining = max(0.0, buy_shares - sell_shares)

    conn.execute(
        """
        UPDATE holdings SET
            avg_price  = CASE WHEN :avg_price IS NOT NULL THEN :avg_price ELSE avg_price END,
            shares     = :shares,
            updated_at = datetime('now')
        WHERE code = :code
        """,
        {"code": code, "avg_price": avg_price, "shares": remaining},
    )
    conn.commit()


# ── watchlist ────────────────────────────────────────────────

def _watchlist_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id":         row["id"],
        "code":       row["code"],
        "name":       row["name"],
        "market":     row["market"],
        "note":       row["note"],
        "created_at": row["created_at"],
    }


def list_watchlist(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM watchlist ORDER BY code").fetchall()
    return [_watchlist_row_to_dict(r) for r in rows]


def get_watchlist_item(conn: sqlite3.Connection, code: str) -> dict | None:
    row = conn.execute("SELECT * FROM watchlist WHERE code = ?", (code,)).fetchone()
    return _watchlist_row_to_dict(row) if row else None


def upsert_watchlist(
    conn: sqlite3.Connection,
    code: str,
    name: str | None = None,
    market: str | None = None,
    note: str | None = None,
) -> dict:
    conn.execute(
        """
        INSERT INTO watchlist (code, name, market, note)
        VALUES (:code, :name, :market, :note)
        ON CONFLICT(code) DO UPDATE SET
            name   = excluded.name,
            market = excluded.market,
            note   = excluded.note
        """,
        {"code": code, "name": name, "market": market, "note": note},
    )
    conn.commit()
    return get_watchlist_item(conn, code)


def delete_watchlist_item(conn: sqlite3.Connection, code: str) -> bool:
    cur = conn.execute("DELETE FROM watchlist WHERE code = ?", (code,))
    conn.commit()
    return cur.rowcount > 0


def watchlist_codes(conn: sqlite3.Connection) -> list[str]:
    """スクリーニング用にコードだけ返す"""
    rows = conn.execute("SELECT code FROM watchlist ORDER BY code").fetchall()
    return [r["code"] for r in rows]


# ── pnl ─────────────────────────────────────────────────────

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


# ──────────────────────────────────────────────────────────
# backtest_runs
# ──────────────────────────────────────────────────────────

def save_backtest_run(conn: sqlite3.Connection, universe: str, metrics: dict, params: dict) -> int:
    """バックテスト結果を backtest_runs テーブルに保存し、採番した id を返す。"""
    import json
    pf = metrics.get("profit_factor", 0)
    if pf == float("inf"):
        pf = None  # SQLite は inf を扱えないため NULL

    curve = metrics.get("equity_curve")
    curve_json = json.dumps(curve, ensure_ascii=False) if curve else None

    cur = conn.execute(
        """
        INSERT INTO backtest_runs
            (universe, n_signals, n_filled, fill_rate, n_closed,
             win_rate, avg_r, profit_factor, max_drawdown_r, time_stop_rate, params,
             sharpe, annual_return_pct, equity_curve)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            universe,
            metrics.get("total_signals"),
            metrics.get("filled"),
            metrics.get("fill_rate"),
            metrics.get("closed"),
            metrics.get("win_rate"),
            metrics.get("avg_r"),
            pf,
            metrics.get("max_drawdown_r"),
            metrics.get("time_stop_rate"),
            json.dumps(params, ensure_ascii=False),
            metrics.get("sharpe_ratio"),
            metrics.get("annual_return_pct"),
            curve_json,
        ),
    )
    conn.commit()
    return cur.lastrowid


def list_backtest_runs(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """直近 limit 件のバックテスト実行履歴を新しい順で返す（equity_curve は除外）。"""
    rows = conn.execute(
        """SELECT id, run_at, universe, n_signals, n_filled, fill_rate, n_closed,
                  win_rate, avg_r, profit_factor, max_drawdown_r, time_stop_rate, params,
                  sharpe, annual_return_pct
           FROM backtest_runs ORDER BY run_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_backtest_run(conn: sqlite3.Connection, run_id: int) -> Optional[dict]:
    """指定 id のバックテスト結果を返す（equity_curve を含む）。存在しない場合は None。"""
    row = conn.execute(
        "SELECT * FROM backtest_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    return dict(row) if row else None


# ── signals（シグナル追跡：実取引のフィードバックループ） ──────────────

_SIGNAL_STATUSES = ("OPEN", "TAKEN", "CLOSED", "SKIPPED", "EXPIRED")


def save_signal(
    conn: sqlite3.Connection,
    code: str,
    side: str,
    name: Optional[str] = None,
    market: Optional[str] = None,
    signal_types: Optional[list] = None,
    score: Optional[float] = None,
    entry_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    target_price: Optional[float] = None,
    entry_kind: Optional[str] = None,
    order_type: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """シグナル（注文プラン）を1件記録する。risk は entry-stop から自動計算。"""
    import json
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side は BUY/SELL のいずれか: {side}")
    risk = None
    if entry_price is not None and stop_price is not None:
        risk = abs(entry_price - stop_price)
    cur = conn.execute(
        """
        INSERT INTO signals
            (code, name, market, side, signal_types, score,
             entry_price, stop_price, target_price, risk, entry_kind, order_type, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            code, name, market, side,
            json.dumps(signal_types, ensure_ascii=False) if signal_types is not None else None,
            score, entry_price, stop_price, target_price, risk, entry_kind, order_type, notes,
        ),
    )
    conn.commit()
    return get_signal(conn, cur.lastrowid)


def get_signal(conn: sqlite3.Connection, signal_id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    return dict(row) if row else None


def list_signals(
    conn: sqlite3.Connection,
    status: Optional[str] = None,
    code: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """シグナルを新しい順で返す。status / code でフィルタ可能。"""
    clauses, params = [], []
    if status:
        clauses.append("status = ?"); params.append(status)
    if code:
        clauses.append("code = ?"); params.append(code)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM signals{where} ORDER BY generated_at DESC, id DESC LIMIT ?",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def update_signal_status(conn: sqlite3.Connection, signal_id: int, status: str) -> Optional[dict]:
    """シグナルの状態を手動更新する（主に SKIPPED / OPEN への切替）。"""
    if status not in _SIGNAL_STATUSES:
        raise ValueError(f"status は {_SIGNAL_STATUSES} のいずれか: {status}")
    if get_signal(conn, signal_id) is None:
        return None
    conn.execute("UPDATE signals SET status = ? WHERE id = ?", (status, signal_id))
    conn.commit()
    return get_signal(conn, signal_id)


def exists_open_signal_today(conn: sqlite3.Connection, code: str, side: str) -> bool:
    """同一銘柄・同一方向の OPEN シグナルが本日分すでにあるか（重複記録の抑止）。"""
    row = conn.execute(
        """SELECT 1 FROM signals
           WHERE code = ? AND side = ? AND status = 'OPEN'
             AND date(generated_at) = date('now') LIMIT 1""",
        (code, side),
    ).fetchone()
    return row is not None


def _on_signal_trade_change(conn: sqlite3.Connection, signal_id: int) -> None:
    """
    シグナルに紐付く取引が増減したときに status と realized_r を再計算する。

    - BUY が1件でもあれば TAKEN（建玉あり）
    - 売却株数 >= 買付株数（>0）なら CLOSED として実現Rを計算
    - 取引が全く無ければ OPEN に戻す
    realized_r = (平均売却単価 − 平均買付単価) / 1株あたりリスク（signal.risk）
    SELL シグナル（手仕舞い指示）は realized_r 計算の対象外（建玉の入口ではないため）。
    """
    sig = get_signal(conn, signal_id)
    if sig is None:
        return

    rows = conn.execute(
        "SELECT side, shares, price FROM trades WHERE signal_id = ?", (signal_id,)
    ).fetchall()

    buy_shares  = sum(r["shares"] for r in rows if r["side"] == "BUY")
    sell_shares = sum(r["shares"] for r in rows if r["side"] == "SELL")
    buy_amount  = sum(r["shares"] * r["price"] for r in rows if r["side"] == "BUY")
    sell_amount = sum(r["shares"] * r["price"] for r in rows if r["side"] == "SELL")

    # 手動で SKIPPED/EXPIRED にしたものは取引が無い限り尊重する
    if not rows:
        new_status = sig["status"] if sig["status"] in ("SKIPPED", "EXPIRED") else "OPEN"
        conn.execute(
            "UPDATE signals SET status = ?, realized_r = NULL WHERE id = ?",
            (new_status, signal_id),
        )
        conn.commit()
        return

    realized_r = None
    status = "TAKEN"
    fully_closed = buy_shares > 0 and sell_shares >= buy_shares
    if fully_closed and sig["side"] == "BUY" and sig.get("risk"):
        avg_buy  = buy_amount / buy_shares
        avg_sell = sell_amount / sell_shares
        realized_r = (avg_sell - avg_buy) / sig["risk"]
        status = "CLOSED"

    conn.execute(
        "UPDATE signals SET status = ?, realized_r = ? WHERE id = ?",
        (status, realized_r, signal_id),
    )
    conn.commit()


def expire_stale_signals(conn: sqlite3.Connection, valid_days: int = 15) -> int:
    """
    OPEN のまま valid_days（暦日換算で余裕を見て 1.5 倍）を過ぎたシグナルを
    EXPIRED にする。エントリー注文の有効期限切れ＝見送りとみなす。
    戻り値: 期限切れにした件数。
    """
    # 営業日15日 ≒ 暦日3週間。安全側に余裕を持たせる。
    calendar_days = int(valid_days * 1.5)
    cur = conn.execute(
        f"""UPDATE signals SET status = 'EXPIRED'
            WHERE status = 'OPEN'
              AND generated_at < datetime('now', '-{calendar_days} days')""",
    )
    conn.commit()
    return cur.rowcount


def signal_attribution(conn: sqlite3.Connection) -> dict:
    """
    シグナル→実取引のアトリビューション集計（ライブ成績）と
    最新バックテスト期待値を並べて返す。

    ライブ側:
        total           : 記録シグナル総数
        taken / skipped / expired / open : 状態別件数
        take_rate       : 終局シグナルのうち約定に至った割合
        closed          : 決済完了（実現R確定）件数
        live_win_rate   : 実現R>0 の割合
        live_avg_r      : 実現Rの平均
    backtest 側（最新の backtest_runs より）:
        bt_win_rate, bt_avg_r, bt_fill_rate, bt_universe, bt_run_at
    """
    rows = conn.execute("SELECT status, realized_r, side FROM signals").fetchall()
    total = len(rows)
    by_status = {s: 0 for s in _SIGNAL_STATUSES}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    taken_like = by_status["TAKEN"] + by_status["CLOSED"]
    terminal = taken_like + by_status["SKIPPED"] + by_status["EXPIRED"]
    take_rate = (taken_like / terminal) if terminal > 0 else None

    closed_rs = [r["realized_r"] for r in rows
                 if r["status"] == "CLOSED" and r["realized_r"] is not None]
    closed = len(closed_rs)
    live_win_rate = (sum(1 for x in closed_rs if x > 0) / closed) if closed > 0 else None
    live_avg_r = (sum(closed_rs) / closed) if closed > 0 else None

    bt = conn.execute(
        """SELECT universe, run_at, win_rate, avg_r, fill_rate
           FROM backtest_runs ORDER BY run_at DESC LIMIT 1"""
    ).fetchone()

    return {
        "total":          total,
        "open":           by_status["OPEN"],
        "taken":          by_status["TAKEN"],
        "closed":         by_status["CLOSED"],
        "skipped":        by_status["SKIPPED"],
        "expired":        by_status["EXPIRED"],
        "take_rate":      take_rate,
        "live_closed":    closed,
        "live_win_rate":  live_win_rate,
        "live_avg_r":     live_avg_r,
        "bt_universe":    bt["universe"] if bt else None,
        "bt_run_at":      bt["run_at"] if bt else None,
        "bt_win_rate":    bt["win_rate"] if bt else None,
        "bt_avg_r":       bt["avg_r"] if bt else None,
        "bt_fill_rate":   bt["fill_rate"] if bt else None,
    }
