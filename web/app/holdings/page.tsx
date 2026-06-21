"use client";

import { useEffect, useState } from "react";
import {
  Holding,
  getHoldings,
  upsertHolding,
  deleteHolding,
  fmtPrice,
} from "@/lib/api";

type FormState = {
  code: string;
  name: string;
  avg_price: string;
  shares: string;
  market: string;
  long_term: boolean;
};

const EMPTY: FormState = {
  code: "",
  name: "",
  avg_price: "",
  shares: "",
  market: "",
  long_term: false,
};

export default function HoldingsPage() {
  const [items, setItems] = useState<Holding[]>([]);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState("");

  const load = () =>
    getHoldings()
      .then(setItems)
      .catch((e) => setError(String(e)));

  useEffect(() => {
    load();
  }, []);

  const startEdit = (h: Holding) => {
    setEditing(true);
    setForm({
      code: h.code,
      name: h.name ?? "",
      avg_price: h.avg_price?.toString() ?? "",
      shares: h.shares?.toString() ?? "",
      market: h.market ?? "",
      long_term: h.long_term,
    });
  };

  const reset = () => {
    setForm(EMPTY);
    setEditing(false);
    setError("");
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (!form.code.trim()) {
      setError("銘柄コードは必須です");
      return;
    }
    try {
      await upsertHolding({
        code: form.code.trim(),
        name: form.name || null,
        avg_price: form.avg_price === "" ? null : Number(form.avg_price),
        shares: form.shares === "" ? null : Number(form.shares),
        market: form.market || null,
        long_term: form.long_term,
      });
      reset();
      load();
    } catch (err) {
      setError(String(err));
    }
  };

  const remove = async (code: string) => {
    if (!confirm(`${code} を削除しますか？`)) return;
    try {
      await deleteHolding(code);
      if (form.code === code) reset();
      load();
    } catch (err) {
      setError(String(err));
    }
  };

  return (
    <div>
      <h1>保有銘柄</h1>

      <div className="panel">
        <h2>{editing ? `編集: ${form.code}` : "新規登録（コードが既存なら上書き）"}</h2>
        <form onSubmit={submit}>
          <div className="row">
            <label>
              コード
              <input
                value={form.code}
                disabled={editing}
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
                style={{ width: 160 }}
              />
            </label>
            <label>
              建値
              <input
                value={form.avg_price}
                inputMode="decimal"
                placeholder="任意"
                onChange={(e) => setForm({ ...form, avg_price: e.target.value })}
                style={{ width: 90 }}
              />
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
              市場
              <select
                value={form.market}
                onChange={(e) => setForm({ ...form, market: e.target.value })}
              >
                <option value="">自動</option>
                <option value="JP">JP</option>
                <option value="US">US</option>
              </select>
            </label>
            <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
              <input
                type="checkbox"
                checked={form.long_term}
                onChange={(e) =>
                  setForm({ ...form, long_term: e.target.checked })
                }
              />
              長期保有（買いのみ通知）
            </label>
            <button type="submit">{editing ? "更新" : "登録"}</button>
            {editing && (
              <button type="button" className="ghost" onClick={reset}>
                キャンセル
              </button>
            )}
          </div>
        </form>
        {error && <div className="error">{error}</div>}
      </div>

      <div className="panel">
        <table>
          <thead>
            <tr>
              <th>コード</th>
              <th>銘柄名</th>
              <th>区分</th>
              <th className="num">建値</th>
              <th className="num">株数</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {items.map((h) => (
              <tr key={h.code} className="clickable" onClick={() => startEdit(h)}>
                <td>{h.code}</td>
                <td>{h.name ?? "-"}</td>
                <td>
                  {h.long_term ? (
                    <span className="tag lt">長期保有</span>
                  ) : (
                    <span className="tag">スイング</span>
                  )}
                </td>
                <td className="num">{fmtPrice(h.code, h.avg_price)}</td>
                <td className="num">{h.shares ?? "-"}</td>
                <td className="num">
                  <button
                    className="danger"
                    onClick={(e) => {
                      e.stopPropagation();
                      remove(h.code);
                    }}
                  >
                    削除
                  </button>
                </td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr>
                <td colSpan={6} className="muted">
                  登録がありません。上のフォームから追加してください。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
