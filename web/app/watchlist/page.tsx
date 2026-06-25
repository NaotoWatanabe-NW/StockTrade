"use client";

import { useEffect, useState } from "react";
import {
  WatchlistItem,
  getWatchlist,
  upsertWatchlistItem,
  deleteWatchlistItem,
  lookupName,
  isJP,
} from "@/lib/api";

type FormState = {
  code: string;
  name: string;
  market: string;
  note: string;
};

const EMPTY: FormState = { code: "", name: "", market: "", note: "" };

export default function WatchlistPage() {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<"ALL" | "JP" | "US">("ALL");
  const [lookingUp, setLookingUp] = useState(false);

  // コード入力後、銘柄名が空なら自動補完する（既存の入力は上書きしない）
  const autoFillName = async () => {
    const code = form.code.trim();
    if (!code || editing || form.name.trim()) return;
    setLookingUp(true);
    try {
      const r = await lookupName(code, form.market || undefined);
      if (r.name) setForm((f) => (f.name.trim() ? f : { ...f, name: r.name! }));
    } catch {
      /* 取得失敗時は無視（手入力できる） */
    } finally {
      setLookingUp(false);
    }
  };

  const load = () =>
    getWatchlist()
      .then(setItems)
      .catch((e) => setError(String(e)));

  useEffect(() => {
    load();
  }, []);

  const startEdit = (item: WatchlistItem) => {
    setEditing(true);
    setForm({
      code: item.code,
      name: item.name ?? "",
      market: item.market ?? "",
      note: item.note ?? "",
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
      await upsertWatchlistItem({
        code: form.code.trim().toUpperCase(),
        name: form.name || null,
        market: form.market || null,
        note: form.note || null,
      });
      reset();
      load();
    } catch (err) {
      setError(String(err));
    }
  };

  const remove = async (code: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`${code} をウォッチリストから削除しますか？`)) return;
    try {
      await deleteWatchlistItem(code);
      if (form.code === code) reset();
      load();
    } catch (err) {
      setError(String(err));
    }
  };

  const marketOf = (item: WatchlistItem) =>
    item.market ?? (isJP(item.code) ? "JP" : "US");

  const filtered = items.filter(
    (it) => filter === "ALL" || marketOf(it) === filter
  );

  const jpCount = items.filter((it) => marketOf(it) === "JP").length;
  const usCount = items.filter((it) => marketOf(it) === "US").length;

  return (
    <div>
      <h1>ウォッチリスト</h1>
      <p className="muted" style={{ marginBottom: 16 }}>
        スクリーニング対象の銘柄を管理します。ここに登録された銘柄がシグナルスキャンの対象になります。
      </p>

      <div className="panel">
        <h2>{editing ? `編集: ${form.code}` : "銘柄を追加（コードが既存なら上書き）"}</h2>
        <form onSubmit={submit}>
          <div className="row">
            <label>
              コード
              <input
                value={form.code}
                disabled={editing}
                placeholder="7203 / AAPL"
                onChange={(e) => setForm({ ...form, code: e.target.value })}
                onBlur={autoFillName}
                style={{ width: 110 }}
              />
            </label>
            <label>
              銘柄名{lookingUp ? "（取得中…）" : ""}
              <input
                value={form.name}
                placeholder="コード入力で自動補完"
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                style={{ width: 160 }}
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
            <label style={{ flex: 1 }}>
              メモ
              <input
                value={form.note}
                placeholder="任意"
                onChange={(e) => setForm({ ...form, note: e.target.value })}
                style={{ width: "100%" }}
              />
            </label>
            <button type="submit">{editing ? "更新" : "追加"}</button>
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
        <div className="row" style={{ marginBottom: 12 }}>
          <span style={{ marginRight: 8 }}>
            合計 {items.length} 銘柄（JP: {jpCount} / US: {usCount}）
          </span>
          {(["ALL", "JP", "US"] as const).map((f) => (
            <button
              key={f}
              className={filter === f ? "" : "ghost"}
              onClick={() => setFilter(f)}
              style={{ marginRight: 4 }}
            >
              {f}
            </button>
          ))}
        </div>
        <table>
          <thead>
            <tr>
              <th>市場</th>
              <th>コード</th>
              <th>銘柄名</th>
              <th>メモ</th>
              <th>追加日</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((item) => (
              <tr
                key={item.code}
                className="clickable"
                onClick={() => startEdit(item)}
              >
                <td>
                  <span className={`tag${marketOf(item) === "JP" ? " lt" : ""}`}>
                    {marketOf(item)}
                  </span>
                </td>
                <td>{item.code}</td>
                <td>{item.name ?? "-"}</td>
                <td className="muted">{item.note ?? ""}</td>
                <td className="muted">
                  {item.created_at ? item.created_at.slice(0, 10) : ""}
                </td>
                <td>
                  <button
                    className="danger"
                    onClick={(e) => remove(item.code, e)}
                  >
                    削除
                  </button>
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={6} className="muted">
                  {items.length === 0
                    ? "登録がありません。上のフォームから追加してください。"
                    : "該当する銘柄がありません。"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
