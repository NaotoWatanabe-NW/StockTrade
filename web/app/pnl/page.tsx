"use client";

import { useEffect, useState } from "react";
import { PnlRow, PnlSummaryRow, getPnl, fmtPrice } from "@/lib/api";

function fmtMoney(currency: string, value: number): string {
  if (currency === "JPY") return `¥${Math.round(value).toLocaleString()}`;
  return `$${value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export default function PnlPage() {
  const [rows, setRows] = useState<PnlRow[]>([]);
  const [summary, setSummary] = useState<PnlSummaryRow[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    getPnl()
      .then((res) => {
        setRows(res.rows);
        setSummary(res.summary);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const taxRate = summary[0]?.tax_rate ?? 0;

  return (
    <div>
      <h1>実現損益</h1>
      <p className="muted">
        約定履歴ベース。実現損益 = 売却額 − 平均取得単価 × 売却株数 − 手数料。
        税金は譲渡益課税（{(taxRate * 100).toFixed(3)}%）を、通貨グループごとの
        損益通算後の純利益に課税して算出しています（利益が出た場合のみ）。
        通貨が異なるため日本株・米国株は別集計です。
      </p>

      {error && <div className="error">{error}</div>}

      <div className="cards" style={{ marginBottom: 20 }}>
        {summary.map((s) => (
          <div className="panel card" key={s.currency}>
            <div className="label">{s.label} 実現損益</div>
            <div className={`value ${s.realized >= 0 ? "pos" : "neg"}`}>
              {fmtMoney(s.currency, s.realized)}
            </div>
            <div className="muted" style={{ marginTop: 6, fontSize: "0.85em" }}>
              税金: −{fmtMoney(s.currency, s.tax)}
              <br />
              税引後: {fmtMoney(s.currency, s.realized_after_tax)}
            </div>
          </div>
        ))}
      </div>

      <div className="panel">
        <table>
          <thead>
            <tr>
              <th>コード</th>
              <th>銘柄名</th>
              <th className="num">買付株</th>
              <th className="num">売却株</th>
              <th className="num">残株</th>
              <th className="num">平均取得単価</th>
              <th className="num">実現損益（税引前）</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.code}>
                <td>{r.code}</td>
                <td>{r.name ?? "-"}</td>
                <td className="num">{r.buy_shares}</td>
                <td className="num">{r.sell_shares}</td>
                <td className="num">{r.remaining_shares}</td>
                <td className="num">{fmtPrice(r.code, r.avg_cost)}</td>
                <td className={`num ${r.realized >= 0 ? "pos" : "neg"}`}>
                  {r.sell_shares > 0 ? fmtPrice(r.code, r.realized) : "-"}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={7} className="muted">
                  約定記録がありません。「取引記録」から登録すると集計されます。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
