#!/usr/bin/env bash
#
# 寝ている保有資産の検出＋Discord通知（cron 用・週1回想定）
#
# 値動きの乏しい保有銘柄を抽出し、戻り売り指値を付けてDiscordへ通知する。
# .env / SQLite(stock.db) を読むので、保有はWebアプリで編集した最新が使われる。
#
# cron 例（毎週月曜の8:00 JSTに実行・場中前に売却候補を確認）:
#   0 8 * * 1 /home/naoto/PythonProject/StockTrade/run_idle_check.sh >> /home/naoto/PythonProject/StockTrade/cron.log 2>&1
#
set -euo pipefail

# スクリプト自身の場所を基準にする（cronのcwdに依存しない）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="$SCRIPT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    echo "仮想環境が見つかりません: $PYTHON" >&2
    echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 寝ている資産チェック ====="
exec "$PYTHON" -m scripts.notify_idle_holdings "$@"
