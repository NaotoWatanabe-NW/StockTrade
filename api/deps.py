"""FastAPI 依存性（DB接続）"""

from data.db import get_connection


def get_db():
    """リクエストごとにDB接続を開き、終了時に閉じる。"""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def auto_expire(conn) -> int:
    """OPEN のまま有効期限を過ぎたシグナルを EXPIRED へ自動遷移する（読み取り時に実行）。

    Web だけで運用しても約定率（take_rate）が滞留しないよう、シグナル一覧・
    アトリビューション・推奨サイジングの読み取り前に呼ぶ。idempotent。
    """
    import config
    from data.repository import expire_stale_signals
    valid_days = int(config.BACKTEST_CONFIG.get("entry_order_valid_days", 15))
    return expire_stale_signals(conn, valid_days)
