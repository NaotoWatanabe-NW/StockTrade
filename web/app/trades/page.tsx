"use client";

import { useEffect, useState } from "react";
import {
  Trade,
  Side,
  getTrades,
  addTrade,
  deleteTrade,
  fmtPrice,
} from "@/lib/api";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

type FormState = {
  code: string;
  name: string;
  side: Side;
  shares: string;
  price: string;
  fee: string;
  traded_at: string;
  note: string;
};

const empty = (): FormState => ({
  code: "",
  name: "",
  side: "BUY",
  shares: "",
  price: "",
  fee: "0",
  traded_at: today(),
  note: "",
});

export default function TradesPage() {
  const [items, setItems] = useState<Trade[]>([]);
  const [form, setForm] = useState<FormState>(empty());
  const [error, setError] = useState("");

  const load = () =>
    getTrades()
      .then(setItems)
      .catch((e) => setError(String(e)));

  useEffect(() => {
    load();
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (!form.code.trim() || form.shares === "" || form.price === "") {
      setError("コード・株数・約定単価は必須です");
      return;
    }
    try {
      await addTrade({
        code: form.code.trim(),
        name: form.name || null,
        side: form.side,
        shares: Number(form.shares),
        price: Number(form.price),
        fee: form.fee === "" ? 0 : Number(form.fee),
        traded_at: form.traded_at,
        note: form.note || null,
      });
      setForm({ ...empty(), code: form.code, traded_at: form.traded_at });
      load();
    } catch (err) {
      setError(String(err));
    }
  };

  const remove = async (id?: number) => {
    if (id === undefined) return;
    if (!confirm("この約定記録を削除しますか？")) return;
    try {
      await deleteTrade(id);
      load();
    } catch (err) {
      setError(String(err));
    }
  };

  return (
    <div>
      <h1>取引記録</h1>

      <div className="panel">
        <h2>約定を記録</h2>
        <form onSubmit={submit}>
          <div className="row">
            <label>
              コード
              <input
                value={form.code}
                placeholder="7203 / AAPL"
                onChange={(e) => setForm({ ...form, code: e.target.value })}
                style={{ width: 110 }}
              />
            </label>
            <label>
              銘柄名
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                style={{ width: 140 }}
              />
            </label>
            <label>
              売買
              <select
                value={form.side}
                onChange={(e) =>
                  setForm({ ...form, side: e.target.value as Side })
                }
              >
                <option value="BUY">買い</option>
                <option value="SELL">売り</option>
              </select>
            </label>
            <label>
              株数
              <input
                value={form.shares}
                inputMode="decimal"
                onChange={(e) => setForm({ ...form, shares: e.target.value })}
                style={{ width: 80 }}
              />
            </label>
            <label>
              約定単価
              <input
                value={form.price}
                inputMode="decimal"
                onChange={(e) => setForm({ ...form, price: e.target.value })}
                style={{ width: 90 }}
              />
            </label>
            <label>
              手数料
              <input
                value={form.fee}
                inputMode="decimal"
                onChange={(e) => setForm({ ...form, fee: e.target.value })}
                style={{ width: 70 }}
              />
            </label>
            <label>
              約定日
              <input
                type="date"
                value={form.traded_at}
                onChange={(e) => setForm({ ...form, traded_at: e.target.value })}
              />
            </label>
            <button type="submit">記録</button>
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <label style={{ flex: 1 }}>
              メモ
              <input
                value={form.note}
                placeholder="任意"
                onChange={(e) => setForm({ ...form, note: e.target.value })}
                style={{ width: "100%" }}
              />
            </label>
          </div>
        </form>
        {error && <div className="error">{error}</div>}
      </div>

      <div className="panel">
        <table>
          <thead>
            <tr>
              <th>約定日</th>
              <th>コード</th>
              <th>銘柄名</th>
              <th>売買</th>
              <th className="num">株数</th>
              <th className="num">単価</th>
              <th className="num">手数料</th>
              <th>メモ</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {items.map((t) => (
              <tr key={t.id}>
                <td>{t.traded_at}</td>
                <td>{t.code}</td>
                <td>{t.name ?? "-"}</td>
                <td className={t.side === "BUY" ? "pos" : "neg"}>
                  {t.side === "BUY" ? "買い" : "売り"}
                </td>
                <td className="num">{t.shares}</td>
                <td className="num">{fmtPrice(t.code, t.price)}</td>
                <td className="num">{fmtPrice(t.code, t.fee)}</td>
                <td className="muted">{t.note ?? ""}</td>
                <td className="num">
                  <button className="danger" onClick={() => remove(t.id)}>
                    削除
                  </button>
                </td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr>
                <td colSpan={9} className="muted">
                  まだ約定記録がありません。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
