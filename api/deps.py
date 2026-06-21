"""FastAPI 依存性（DB接続）"""

from data.db import get_connection


def get_db():
    """リクエストごとにDB接続を開き、終了時に閉じる。"""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()
