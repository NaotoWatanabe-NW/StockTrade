import Link from "next/link";

export default function Nav() {
  return (
    <nav className="nav">
      <span className="brand">📈 StockTrade</span>
      <Link href="/">ダッシュボード</Link>
      <Link href="/holdings">保有銘柄</Link>
      <Link href="/watchlist">ウォッチリスト</Link>
      <Link href="/trades">取引記録</Link>
      <Link href="/pnl">損益</Link>
      <Link href="/signals">シグナル</Link>
      <Link href="/backtest">バックテスト</Link>
    </nav>
  );
}
