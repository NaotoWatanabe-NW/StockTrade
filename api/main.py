"""
取引記録 Web アプリのバックエンド（FastAPI）

保有銘柄の管理と約定履歴の記録・損益集計を提供する。
フロントエンド（Next.js, localhost:3000）からのCORSを許可。

起動:
    .venv/bin/uvicorn api.main:app --reload --port 8000
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import holdings, trades, pnl, backtest, portfolio, signals

app = FastAPI(title="StockTrade API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    # ローカル開発用に localhost/127.0.0.1 の任意ポートを許可（オリジン差異での
    # CORSブロック＝NetworkError を防ぐ）
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(holdings.router)
app.include_router(trades.router)
app.include_router(pnl.router)
app.include_router(backtest.router)
app.include_router(portfolio.router)
app.include_router(signals.router)


@app.get("/api/health", tags=["health"])
def health():
    return {"status": "ok"}
