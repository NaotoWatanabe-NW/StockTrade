"use client";

import { useEffect, useState } from "react";
import { PnlRow, getPnl, fmtPrice, isJP } from "@/lib/api";

export default function PnlPage() {
  const [rows, setRows] = useState<PnlRow[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    getPnl()
      .then(setRows)
      .catch((e) => setError(String(e)));
  }, []);

  const jpTotal = rows
    .filter((r) => isJP(r.code))
    .reduce((s, r) => s + r.realized, 0);
  const usTotal = rows
    .filter((r) => !isJP(r.code))
    .reduce((s, r) => s + r.realized, 0);

  return (
    <div>
      <h1>実現損益</h1>
      <p className="muted">
        約定履歴ベース。実現損益 = 売却額 − 平均取得単価 × 売却株数 − 手数料。
        通貨が異なるため日本株・米国株は別集計です。
      </p>

      {error && <div className="error">{error}</div>}

      <div className="cards" style={{ marginBottom: 20 }}>
        <div className="panel card">
          <div className="label">日本株 実現損益合計</div>
          <div className={`value ${jpTotal >= 0 ? "pos" : "neg"}`}>
            ¥{Math.round(jpTotal).toLocaleString()}
          </div>
        </div>
        <div className="panel card">
          <div className="label">米国株 実現損益合計</div>
          <div className={`value ${usTotal >= 0 ? "pos" : "neg"}`}>
            ${usTotal.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
        </div>
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
              <th className="num">実現損益</th>
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
