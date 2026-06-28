"""株式分割・併合に伴い、保有株数・平均取得単価・約定履歴を調整するツール。

`--ratio A:B` は「A株 → B株」を表す（総額＝株数×単価 は不変）:
  - 株式分割 1株→2株 : `--ratio 1:2`（株数2倍・単価1/2）
  - 株式分割 1株→3株 : `--ratio 1:3`
  - 株式併合 4株→1株 : `--ratio 4:1`（株数1/4・単価4倍）

調整対象:
  - 保有(holdings) … shares ×(B/A)、avg_price ×(A/B)
  - 約定(trades)   … `--date`（効力発生日）より前の約定のみ shares/price を同様に調整
                     （実現損益の金額は不変。保有⇄約定の整合も保たれる）
  - price_history  … 当該コードのキャッシュを破棄（次回取得で分割調整後の価格を取り直す）
  ※ 過去シグナルの計画値(signals)は対象外。

実行:
    python -m scripts.adjust_shares --code 7203 --ratio 1:2             # 1株→2株(分割)
    python -m scripts.adjust_shares --code 6758 --ratio 4:1             # 4株→1株(併合)
    python -m scripts.adjust_shares --code 7203 --ratio 1:2 --dry-run   # 変更プレビュー
    python -m scripts.adjust_shares --code 7203 --ratio 1:2 --date 2026-07-01
"""

import sys
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.db import get_connection
from data.repository import apply_share_adjustment

log = logging.getLogger(__name__)


def _parse_ratio(text: str) -> tuple[float, float]:
    """"A:B"（A株→B株）を (from_shares, to_shares) に解釈する。"""
    if ":" not in text:
        raise argparse.ArgumentTypeError("--ratio は A:B 形式で指定してください（例 1:2 / 4:1）")
    a, _, b = text.partition(":")
    try:
        from_shares, to_shares = float(a), float(b)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--ratio の数値が不正です: {text}")
    if from_shares <= 0 or to_shares <= 0:
        raise argparse.ArgumentTypeError("--ratio の両辺は正の数で指定してください")
    return from_shares, to_shares


def _fmt(v) -> str:
    if v is None:
        return "—"
    return f"{v:,.4f}".rstrip("0").rstrip(".")


def _print_report(r: dict, dry_run: bool) -> None:
    frm, to = r["from_shares"], r["to_shares"]
    kind = "分割" if to > frm else ("併合" if to < frm else "変更なし")
    print(f"■ {r['code']} 株式{kind}  {_fmt(frm)}株 → {_fmt(to)}株"
          f"（株数 ×{_fmt(r['share_mult'])} / 単価 ×{_fmt(r['price_mult'])}）"
          + ("  ※--dry-run（未保存）" if dry_run else ""))
    print(f"  効力発生日: {r['effective_date']} より前の約定が対象")

    hb, ha = r["holding_before"], r["holding_after"]
    if hb is None:
        print("  保有: 登録なし")
    else:
        print(f"  保有株数: {_fmt(hb['shares'])} → {_fmt(ha['shares'])}")
        print(f"  平均単価: {_fmt(hb['avg_price'])} → {_fmt(ha['avg_price'])}")
        if ha["shares"] is not None and abs(ha["shares"] - round(ha["shares"])) > 1e-9:
            print("  ⚠️ 調整後の株数が整数になりません（併合の端株など）。"
                  "実際の保有に合わせて手動調整してください。")
    print(f"  調整した約定: {r['trades_adjusted']} 件")
    if not dry_run:
        print(f"  価格キャッシュ破棄: {r['cache_cleared']} 行（次回取得で再取得）")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="株式分割・併合に伴う株数・単価の調整")
    parser.add_argument("--code", required=True, help="証券コード/ティッカー（例 7203 / AAPL）")
    parser.add_argument("--ratio", required=True, type=_parse_ratio,
                        help="A:B（A株→B株）。分割 1:2 / 併合 4:1 など")
    parser.add_argument("--date", default=None,
                        help="効力発生日 YYYY-MM-DD（この日より前の約定を調整。既定: 当日）")
    parser.add_argument("--keep-cache", action="store_true",
                        help="price_history キャッシュを破棄しない")
    parser.add_argument("--dry-run", action="store_true",
                        help="DBに保存せず変更内容のみ表示")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from_shares, to_shares = args.ratio
    conn = get_connection()
    try:
        report = apply_share_adjustment(
            conn, args.code, from_shares, to_shares,
            effective_date=args.date,
            clear_cache=not args.keep_cache,
            dry_run=args.dry_run,
        )
    finally:
        conn.close()

    if report["holding_before"] is None and report["trades_adjusted"] == 0:
        print(f"■ {args.code}: 調整対象（保有・約定）が見つかりませんでした。")
        return 0

    _print_report(report, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
