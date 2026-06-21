#!/usr/bin/env bash
#
# Webアプリ起動スクリプト
#
# バックエンド（FastAPI）とフロントエンド（Next.js）を空きポートで同時起動。
# ポートは起動のたびにランダムに選ばれるので競合しにくい。
# Ctrl-C で両方まとめて停止する。
#
# 使い方:
#   ./run_web.sh           # 開発モード（--reload / npm run dev）
#   ./run_web.sh --prod    # 本番モード（ビルド済み next start）
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="$SCRIPT_DIR/.venv/bin/python"
UVICORN="$SCRIPT_DIR/.venv/bin/uvicorn"
WEB_DIR="$SCRIPT_DIR/web"
PROD=${1:-}

if [[ ! -x "$PYTHON" ]]; then
    echo "仮想環境が見つかりません: $PYTHON" >&2
    echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

# 空きポートを1つ取得（OSにバインドさせてすぐ閉じる）
free_port() {
    "$PYTHON" -c "
import socket
s = socket.socket()
s.bind(('', 0))
print(s.getsockname()[1])
s.close()
"
}

API_PORT=$(free_port)
WEB_PORT=$(free_port)

# フロントエンドがバックエンドURLを知るために .env.local を更新
echo "NEXT_PUBLIC_API_BASE=http://localhost:${API_PORT}" > "$WEB_DIR/.env.local"

if [[ ! -d "$WEB_DIR/node_modules" ]]; then
    echo "▶ npm install 実行中..."
    (cd "$WEB_DIR" && npm install)
fi

# PIDを明示的に管理して Ctrl-C で両方まとめて停止
API_PID=
WEB_PID=

cleanup() {
    echo ""
    echo "⏹  停止中..."
    [[ -n "$API_PID" ]] && kill "$API_PID" 2>/dev/null || true
    [[ -n "$WEB_PID" ]] && kill "$WEB_PID" 2>/dev/null || true
    wait "$API_PID" "$WEB_PID" 2>/dev/null || true
}
trap cleanup INT TERM

echo "===== $(date '+%Y-%m-%d %H:%M:%S') Webアプリ起動 ====="
echo "  バックエンド → http://localhost:${API_PORT}"
echo "  フロントエンド → http://localhost:${WEB_PORT}"
echo "  API docs      → http://localhost:${API_PORT}/docs"
echo "  Ctrl-C で両方停止"
echo ""

if [[ "$PROD" == "--prod" ]]; then
    echo "▶ [本番] フロントエンドをビルド中..."
    (cd "$WEB_DIR" && npm run build)
    "$UVICORN" api.main:app --host 0.0.0.0 --port "$API_PORT" &
    API_PID=$!
    (cd "$WEB_DIR" && npm run start -- --port "$WEB_PORT") &
    WEB_PID=$!
else
    "$UVICORN" api.main:app --reload --port "$API_PORT" &
    API_PID=$!
    (cd "$WEB_DIR" && npm run dev -- --port "$WEB_PORT") &
    WEB_PID=$!
fi

wait "$API_PID" "$WEB_PID"
