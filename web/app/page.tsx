"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Holding,
  Trade,
  PnlRow,
  getHoldings,
  getTrades,
  getPnl,
  isJP,
} from "@/lib/api";

export default function DashboardPage() {
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [pnl, setPnl] = useState<PnlRow[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    // 1つのAPIが落ちても他の表示は壊さない（部分失敗時のみエラー表示）
    Promise.allSettled([getHoldings(), getTrades(), getPnl()]).then(
      ([h, t, p]) => {
        if (h.status === "fulfilled") setHoldings(h.value);
        if (t.status === "fulfilled") setTrades(t.value);
        if (p.status === "fulfilled") setPnl(p.value);
        const failed = [h, t, p].find((r) => r.status === "rejected") as
          | PromiseRejectedResult
          | undefined;
        if (failed) setError(String(failed.reason.message ?? failed.reason));
      }
    );
  }, []);

  const longTerm = holdings.filter((h) => h.long_term).length;
  const jpRealized = pnl
    .filter((r) => isJP(r.code))
    .reduce((s, r) => s + r.realized, 0);
  const usRealized = pnl
    .filter((r) => !isJP(r.code))
    .reduce((s, r) => s + r.realized, 0);

  return (
    <div>
      <h1>ダッシュボード</h1>
      {error && (
        <div className="error">
          {error}
          <div className="muted" style={{ marginTop: 4 }}>
            バックエンド（uvicorn api.main:app --port 8000）が起動しているか確認してください。
          </div>
        </div>
      )}

      <div className="cards">
        <div className="panel card">
          <div className="label">保有銘柄</div>
          <div className="value">{holdings.length}</div>
          <div className="muted">うち長期保有 {longTerm}</div>
        </div>
        <div className="panel card">
          <div className="label">約定記録</div>
          <div className="value">{trades.length}</div>
        </div>
        <div className="panel card">
          <div className="label">実現損益（日本株）</div>
          <div className={`value ${jpRealized >= 0 ? "pos" : "neg"}`}>
            ¥{Math.round(jpRealized).toLocaleString()}
          </div>
        </div>
        <div className="panel card">
          <div className="label">実現損益（米国株）</div>
          <div className={`value ${usRealized >= 0 ? "pos" : "neg"}`}>
            ${usRealized.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
        </div>
      </div>

      <div className="panel" style={{ marginTop: 20 }}>
        <h2>クイックリンク</h2>
        <div className="row">
          <Link href="/holdings">
            <button>保有銘柄を管理</button>
          </Link>
          <Link href="/trades">
            <button>約定を記録</button>
          </Link>
          <Link href="/pnl">
            <button className="ghost">損益を見る</button>
          </Link>
        </div>
      </div>
    </div>
  );
}
