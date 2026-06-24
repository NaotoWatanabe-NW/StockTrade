"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Holding,
  Trade,
  PnlSummaryRow,
  PortfolioHeat,
  getHoldings,
  getTrades,
  getPnl,
  getPortfolioHeat,
} from "@/lib/api";

export default function DashboardPage() {
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [pnlSummary, setPnlSummary] = useState<PnlSummaryRow[]>([]);
  const [heat, setHeat] = useState<PortfolioHeat | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    // 1つのAPIが落ちても他の表示は壊さない（部分失敗時のみエラー表示）
    Promise.allSettled([getHoldings(), getTrades(), getPnl(), getPortfolioHeat()]).then(
      ([h, t, p, hh]) => {
        if (h.status === "fulfilled") setHoldings(h.value);
        if (t.status === "fulfilled") setTrades(t.value);
        if (p.status === "fulfilled") setPnlSummary(p.value.summary);
        if (hh.status === "fulfilled") setHeat(hh.value);
        const failed = [h, t, p, hh].find((r) => r.status === "rejected") as
          | PromiseRejectedResult
          | undefined;
        if (failed) setError(String(failed.reason.message ?? failed.reason));
      }
    );
  }, []);

  const longTerm = holdings.filter((h) => h.long_term).length;
  const jp = pnlSummary.find((s) => s.currency === "JPY");
  const us = pnlSummary.find((s) => s.currency === "USD");
  const jpRealized = jp?.realized_after_tax ?? 0;
  const usRealized = us?.realized_after_tax ?? 0;

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
          <div className="label">実現損益（日本株・税引後）</div>
          <div className={`value ${jpRealized >= 0 ? "pos" : "neg"}`}>
            ¥{Math.round(jpRealized).toLocaleString()}
          </div>
          {jp && jp.tax > 0 && (
            <div className="muted">税金 −¥{Math.round(jp.tax).toLocaleString()}</div>
          )}
        </div>
        <div className="panel card">
          <div className="label">実現損益（米国株・税引後）</div>
          <div className={`value ${usRealized >= 0 ? "pos" : "neg"}`}>
            ${usRealized.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          {us && us.tax > 0 && (
            <div className="muted">税金 −${us.tax.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
          )}
        </div>
        {heat && (
          <div className="panel card">
            <div className="label">ポートフォリオ熱量</div>
            <div className={`value ${heat.heat_pct >= heat.heat_max_pct ? "neg" : heat.heat_pct >= heat.heat_max_pct * 0.8 ? "" : "pos"}`}>
              {heat.heat_pct.toFixed(1)}%
            </div>
            <div className="muted">
              上限 {heat.heat_max_pct.toFixed(1)}%（{heat.open_positions}/{heat.max_positions}件）
            </div>
          </div>
        )}
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
          <Link href="/backtest">
            <button className="ghost">バックテスト履歴</button>
          </Link>
        </div>
      </div>
    </div>
  );
}
