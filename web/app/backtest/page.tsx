"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { BacktestRun, getBacktestRuns } from "@/lib/api";

function pct(v: number | null | undefined, decimals = 1) {
  if (v == null) return "-";
  return `${(v * 100).toFixed(decimals)}%`;
}

function r2(v: number | null | undefined) {
  if (v == null) return "-";
  return v >= 0 ? `+${v.toFixed(3)}R` : `${v.toFixed(3)}R`;
}

function pf(v: number | null | undefined) {
  if (v == null) return "-";
  return v.toFixed(2);
}

function fmtAnnual(v: number | null | undefined) {
  if (v == null) return "-";
  return v >= 0 ? `+${v.toFixed(1)}%` : `${v.toFixed(1)}%`;
}

function rowColor(win_rate: number | null | undefined): string {
  if (win_rate == null) return "";
  if (win_rate >= 0.55) return "pos";
  if (win_rate < 0.45) return "neg";
  return "";
}

export default function BacktestPage() {
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);

  useEffect(() => {
    getBacktestRuns(30)
      .then(setRuns)
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <div>
      <h1>バックテスト履歴</h1>
      <p className="muted" style={{ marginBottom: 16 }}>
        <code>python -m backtest.runner --universe JP --save</code>{" "}
        を実行すると結果がここに蓄積されます。
      </p>

      {error && <div className="error">{error}</div>}

      {runs.length === 0 && !error && (
        <div className="panel muted">バックテスト結果がまだありません。</div>
      )}

      {runs.length > 0 && (
        <div className="panel" style={{ overflowX: "auto" }}>
          <table>
            <thead>
              <tr>
                <th>実行日時</th>
                <th>対象</th>
                <th className="num">シグナル</th>
                <th className="num">約定率</th>
                <th className="num">勝率</th>
                <th className="num">平均R</th>
                <th className="num">PF</th>
                <th className="num">最大DD</th>
                <th className="num">Sharpe</th>
                <th className="num">年率</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <>
                  <tr key={r.id} className={rowColor(r.win_rate)}>
                    <td className="muted">
                      <Link href={`/backtest/${r.id}`} style={{ textDecoration: "none" }}>
                        {r.run_at.slice(0, 16)}
                      </Link>
                    </td>
                    <td>{r.universe}</td>
                    <td className="num">{r.n_signals ?? "-"}</td>
                    <td className="num">{pct(r.fill_rate)}</td>
                    <td className={`num ${rowColor(r.win_rate)}`}>
                      {pct(r.win_rate)}
                    </td>
                    <td className={`num ${(r.avg_r ?? 0) >= 0 ? "pos" : "neg"}`}>
                      {r2(r.avg_r)}
                    </td>
                    <td className="num">{pf(r.profit_factor)}</td>
                    <td className="num neg">{r2(r.max_drawdown_r)}</td>
                    <td className={`num ${(r.sharpe ?? 0) >= 1 ? "pos" : (r.sharpe ?? 0) < 0 ? "neg" : ""}`}>
                      {r.sharpe != null ? r.sharpe.toFixed(2) : "-"}
                    </td>
                    <td className={`num ${(r.annual_return_pct ?? 0) >= 0 ? "pos" : "neg"}`}>
                      {fmtAnnual(r.annual_return_pct)}
                    </td>
                    <td>
                      <div className="row" style={{ gap: 4 }}>
                        <Link href={`/backtest/${r.id}`}>
                          <button className="ghost" style={{ fontSize: 12, padding: "2px 8px" }}>
                            資産曲線
                          </button>
                        </Link>
                        {r.params && (
                          <button
                            className="ghost"
                            style={{ fontSize: 12, padding: "2px 8px" }}
                            onClick={() =>
                              setExpanded(expanded === r.id ? null : r.id)
                            }
                          >
                            {expanded === r.id ? "▲" : "▼"} params
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                  {expanded === r.id && r.params && (
                    <tr key={`${r.id}-params`}>
                      <td colSpan={11}>
                        <pre
                          style={{
                            fontSize: 12,
                            background: "var(--bg2)",
                            padding: 12,
                            borderRadius: 4,
                            overflowX: "auto",
                          }}
                        >
                          {JSON.stringify(JSON.parse(r.params), null, 2)}
                        </pre>
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="panel" style={{ marginTop: 20 }}>
        <h2>CLIコマンド早見表</h2>
        <table>
          <tbody>
            <tr>
              <td><code>--universe JP --save</code></td>
              <td className="muted">日本株バックテストを実行して保存</td>
            </tr>
            <tr>
              <td><code>--universe US --save</code></td>
              <td className="muted">米国株バックテストを実行して保存</td>
            </tr>
            <tr>
              <td><code>--no-regime --save</code></td>
              <td className="muted">レジームフィルタ無効で比較用に保存</td>
            </tr>
            <tr>
              <td><code>--optimize</code></td>
              <td className="muted">ウォークフォワード最適化を実行</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
