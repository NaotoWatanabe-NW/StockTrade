// FastAPI バックエンドのクライアント

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type Holding = {
  id?: number;
  code: string;
  name?: string | null;
  avg_price?: number | null;
  shares?: number | null;
  market?: string | null; // "JP"/"US"/null
  long_term: boolean;
};

export type Side = "BUY" | "SELL";

export type Trade = {
  id?: number;
  code: string;
  name?: string | null;
  side: Side;
  shares: number;
  price: number;
  fee: number;
  traded_at: string; // YYYY-MM-DD
  note?: string | null;
};

export type PnlRow = {
  code: string;
  name?: string | null;
  buy_shares: number;
  sell_shares: number;
  remaining_shares: number;
  avg_cost: number;
  buy_amount: number;
  sell_amount: number;
  fee_total: number;
  realized: number;
};

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      signal: controller.signal,
      ...init,
    });
  } catch (e) {
    // 接続不可・タイムアウト・CORS等の低レベル失敗を分かりやすいメッセージに変換
    throw new Error(
      `APIに接続できません（${API_BASE}）。バックエンドが起動しているか確認してください。`
    );
  } finally {
    clearTimeout(timeout);
  }
  if (!res.ok) {
    let detail = await res.text();
    try {
      detail = JSON.parse(detail).detail ?? detail;
    } catch {
      /* プレーンテキストのまま */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const getHoldings = () => req<Holding[]>("/api/holdings");
export const upsertHolding = (h: Holding) =>
  req<Holding>("/api/holdings", { method: "POST", body: JSON.stringify(h) });
export const deleteHolding = (code: string) =>
  req<{ deleted: string }>(`/api/holdings/${encodeURIComponent(code)}`, {
    method: "DELETE",
  });

export const getTrades = (code?: string) =>
  req<Trade[]>(`/api/trades${code ? `?code=${encodeURIComponent(code)}` : ""}`);
export const addTrade = (t: Trade) =>
  req<Trade>("/api/trades", { method: "POST", body: JSON.stringify(t) });
export const deleteTrade = (id: number) =>
  req<{ deleted: number }>(`/api/trades/${id}`, { method: "DELETE" });

export const getPnl = () => req<PnlRow[]>("/api/pnl");

// ── 表示ヘルパー（市場・通貨） ──────────────────────
export function isJP(code: string): boolean {
  // 数字のみ（東証コード）→ 日本株。英字を含めば米国株
  return /^[0-9.]+$/.test(code);
}

export function fmtPrice(code: string, value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  if (isJP(code)) return `¥${Math.round(value).toLocaleString()}`;
  return `$${value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}
