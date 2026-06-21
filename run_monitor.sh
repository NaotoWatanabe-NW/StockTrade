#!/usr/bin/env bash
#
# 株式監視ツールの定期実行スクリプト（cron 用）
#
# 1回分のチェック（保有監視＋スクリーニング → Discord通知）を実行する。
# .env / SQLite(stock.db) を読むので、保有はWebアプリで編集した最新が使われる。
#
# cron 例（平日の9:30と15:30 JSTに実行）:
#   30 9,15 * * 1-5 /home/naoto/PythonProject/StockTrade/run_monitor.sh >> /home/naoto/PythonProject/StockTrade/cron.log 2>&1
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

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 監視実行 ====="
exec "$PYTHON" main.py "$@"
