"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { BacktestRun, EquityPoint, getBacktestRun } from "@/lib/api";

// ── SVG 資産曲線コンポーネント ────────────────────────────
function EquityChart({ points }: { points: EquityPoint[] }) {
  if (points.length < 2) return <p className="muted">データ不足（トレード数が少なすぎます）</p>;

  const W = 800;
  const H = 320;
  const PAD = { top: 20, right: 20, bottom: 40, left: 60 };
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;

  const equities = points.map((p) => p.equity);
  const minE = Math.min(...equities);
  const maxE = Math.max(...equities);
  const range = maxE - minE || 0.01;

  const xScale = (i: number) => (i / (points.length - 1)) * innerW;
  const yScale = (v: number) => innerH - ((v - minE) / range) * innerH;

  const pathD = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${xScale(i).toFixed(1)} ${yScale(p.equity).toFixed(1)}`)
    .join(" ");

  const baseline = yScale(1.0);
  const finalEquity = equities[equities.length - 1];
  const lineColor = finalEquity >= 1.0 ? "var(--pos, #4ade80)" : "var(--neg, #f87171)";

  // X 軸ラベル（最大 6 点）
  const xLabels: { i: number; label: string }[] = [];
  const step = Math.max(1, Math.floor(points.length / 6));
  for (let i = 0; i < points.length; i += step) {
    xLabels.push({ i, label: points[i].date.slice(0, 7) }); // YYYY-MM
  }
  // 常に最後のラベルを含める
  if (xLabels[xLabels.length - 1]?.i !== points.length - 1) {
    xLabels.push({ i: points.length - 1, label: points[points.length - 1].date.slice(0, 7) });
  }

  // Y 軸ラベル（5 本）
  const yLabels = Array.from({ length: 5 }, (_, i) => {
    const v = minE + (range * i) / 4;
    return { v, label: `×${v.toFixed(2)}` };
  });

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      style={{ width: "100%", maxWidth: W, height: "auto", display: "block" }}
    >
      <g transform={`translate(${PAD.left},${PAD.top})`}>
        {/* グリッド + Y ラベル */}
        {yLabels.map(({ v, label }) => (
          <g key={label}>
            <line
              x1={0} y1={yScale(v)} x2={innerW} y2={yScale(v)}
              stroke="var(--border, #334155)" strokeWidth={0.5}
            />
            <text
              x={-6} y={yScale(v) + 4}
              textAnchor="end" fontSize={11} fill="var(--muted, #94a3b8)"
            >
              {label}
            </text>
          </g>
        ))}

        {/* 1.0 基準線 */}
        {baseline >= 0 && baseline <= innerH && (
          <line
            x1={0} y1={baseline} x2={innerW} y2={baseline}
            stroke="var(--muted, #94a3b8)" strokeWidth={1} strokeDasharray="4 3"
          />
        )}

        {/* 資産曲線 */}
        <path d={pathD} fill="none" stroke={lineColor} strokeWidth={1.8} />

        {/* X 軸ラベル */}
        {xLabels.map(({ i, label }) => (
          <text
            key={i} x={xScale(i)} y={innerH + 20}
            textAnchor="middle" fontSize={11} fill="var(--muted, #94a3b8)"
          >
            {label}
          </text>
        ))}
      </g>
    </svg>
  );
}

// ── メトリクス行ヘルパー ──────────────────────────────────
function Row({ label, value, cls = "" }: { label: string; value: string; cls?: string }) {
  return (
    <tr>
      <td className="muted" style={{ paddingRight: 24 }}>{label}</td>
      <td className={`num ${cls}`}>{value}</td>
    </tr>
  );
}

function pct(v: number | null | undefined) {
  if (v == null) return "-";
  return `${(v * 100).toFixed(1)}%`;
}
function r2(v: number | null | undefined) {
  if (v == null) return "-";
  return v >= 0 ? `+${v.toFixed(3)}R` : `${v.toFixed(3)}R`;
}
function pf(v: number | null | undefined) {
  return v == null ? "-" : v.toFixed(2);
}
function ann(v: number | null | undefined) {
  if (v == null) return "-";
  return v >= 0 ? `+${v.toFixed(1)}%` : `${v.toFixed(1)}%`;
}

// ── ページ本体 ────────────────────────────────────────────
export default function BacktestDetailPage({ params }: { params: { id: string } }) {
  const [run, setRun] = useState<BacktestRun | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getBacktestRun(Number(params.id))
      .then(setRun)
      .catch((e) => setError(String(e)));
  }, [params.id]);

  const curve: EquityPoint[] = run?.equity_curve
    ? JSON.parse(run.equity_curve)
    : [];

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <Link href="/backtest">← バックテスト履歴に戻る</Link>
      </div>

      {error && <div className="error">{error}</div>}
      {!run && !error && <div className="muted">読み込み中...</div>}

      {run && (
        <>
          <h1>
            バックテスト詳細 #{run.id}
            <span className="muted" style={{ fontSize: 16, marginLeft: 12, fontWeight: "normal" }}>
              {run.universe} / {run.run_at.slice(0, 16)}
            </span>
          </h1>

          {/* 資産曲線 */}
          <div className="panel" style={{ marginBottom: 20 }}>
            <h2>資産曲線（1.0 始点、複利）</h2>
            {curve.length > 0 ? (
              <EquityChart points={curve} />
            ) : (
              <p className="muted">資産曲線データがありません（旧バックテスト結果）。</p>
            )}
          </div>

          {/* メトリクス */}
          <div className="panel">
            <h2>パフォーマンス指標</h2>
            <table>
              <tbody>
                <Row label="シグナル数" value={String(run.n_signals ?? "-")} />
                <Row label="約定率" value={pct(run.fill_rate)} />
                <Row label="決済数" value={String(run.n_closed ?? "-")} />
                <Row label="勝率" value={pct(run.win_rate)}
                  cls={(run.win_rate ?? 0) >= 0.55 ? "pos" : (run.win_rate ?? 0) < 0.45 ? "neg" : ""} />
                <Row label="平均損益 (R)" value={r2(run.avg_r)}
                  cls={(run.avg_r ?? 0) >= 0 ? "pos" : "neg"} />
                <Row label="プロフィットファクター" value={pf(run.profit_factor)} />
                <Row label="最大ドローダウン (R)" value={r2(run.max_drawdown_r)} cls="neg" />
                <Row label="シャープレシオ" value={run.sharpe != null ? run.sharpe.toFixed(2) : "-"}
                  cls={(run.sharpe ?? 0) >= 1 ? "pos" : (run.sharpe ?? 0) < 0 ? "neg" : ""} />
                <Row label="年率リターン" value={ann(run.annual_return_pct)}
                  cls={(run.annual_return_pct ?? 0) >= 0 ? "pos" : "neg"} />
                <Row label="タイムストップ率" value={pct(run.time_stop_rate)} />
              </tbody>
            </table>
          </div>

          {/* パラメータ */}
          {run.params && (
            <div className="panel" style={{ marginTop: 20 }}>
              <h2>使用パラメータ</h2>
              <pre style={{ fontSize: 13, overflowX: "auto" }}>
                {JSON.stringify(JSON.parse(run.params), null, 2)}
              </pre>
            </div>
          )}
        </>
      )}
    </div>
  );
}
