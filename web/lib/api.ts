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
  signal_id?: number | null; // 紐付くシグナル（予測ベース注文）。null=単独注文
};

export type WatchlistItem = {
  id?: number;
  code: string;
  name?: string | null;
  market?: string | null;
  note?: string | null;
  created_at?: string;
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

export type PnlSummaryRow = {
  currency: string;
  label: string;
  realized: number;
  tax: number;
  realized_after_tax: number;
  tax_rate: number;
};

export type PnlResponse = {
  rows: PnlRow[];
  summary: PnlSummaryRow[];
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

export const getPnl = () => req<PnlResponse>("/api/pnl");

export type NameLookup = { code: string; name: string | null; source: string | null };

export const lookupName = (code: string, market?: string) =>
  req<NameLookup>(
    `/api/lookup/name?code=${encodeURIComponent(code)}` +
      (market ? `&market=${encodeURIComponent(market)}` : "")
  );

export const getWatchlist = () => req<WatchlistItem[]>("/api/watchlist");
export const upsertWatchlistItem = (item: WatchlistItem) =>
  req<WatchlistItem>("/api/watchlist", { method: "POST", body: JSON.stringify(item) });
export const deleteWatchlistItem = (code: string) =>
  req<{ deleted: string }>(`/api/watchlist/${encodeURIComponent(code)}`, {
    method: "DELETE",
  });

export type BacktestRun = {
  id: number;
  run_at: string;
  universe: string;
  n_signals?: number | null;
  n_filled?: number | null;
  fill_rate?: number | null;
  n_closed?: number | null;
  win_rate?: number | null;
  avg_r?: number | null;
  profit_factor?: number | null;
  max_drawdown_r?: number | null;
  time_stop_rate?: number | null;
  params?: string | null;
  sharpe?: number | null;
  annual_return_pct?: number | null;
  equity_curve?: string | null;  // JSON 文字列 → parse して使う
  status: string;                // running / done / error
  error?: string | null;
};

export type BacktestRunRequest = {
  universe: string;              // "JP" / "US" / "ALL"
  regime: boolean;               // レジームフィルタ
  no_partial_tp: boolean;        // 部分利確を無効化
  min_score?: number | null;     // スコア絶対値フィルタ
  params: Record<string, number | boolean>; // 個別パラメータ上書き
};

export type EquityPoint = { date: string; equity: number };

export type PortfolioHeat = {
  open_positions: number;
  max_positions: number;
  risk_per_trade_pct: number;
  heat_pct: number;
  heat_max_pct: number;
};

export const getBacktestRuns = (limit = 20) =>
  req<BacktestRun[]>(`/api/backtest?limit=${limit}`);

export const getBacktestRun = (id: number) =>
  req<BacktestRun>(`/api/backtest/${id}`);

export const getBacktestDefaults = () =>
  req<Record<string, number | boolean>>("/api/backtest/defaults");

export const runBacktest = (body: BacktestRunRequest) =>
  req<BacktestRun>("/api/backtest/run", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const getPortfolioHeat = () =>
  req<PortfolioHeat>("/api/portfolio/heat");

export type SizingSuggestion = {
  signal_id: number;
  code: string;
  name?: string | null;
  market?: string | null;
  score?: number | null;
  entry_price?: number | null;
  stop_price?: number | null;
  target_price?: number | null;
  lot_size: number;
  suggested_shares: number;
  investment: number;
  risk_amount: number;
};

export type SizingResponse = {
  account_size: number;
  risk_per_trade_pct: number;
  max_positions: number;
  open_positions: number;
  remaining_slots: number;
  heat_pct: number;
  suggestions: SizingSuggestion[];
};

export const getSizingSuggestions = () =>
  req<SizingResponse>("/api/portfolio/suggestions");

export type SignalStatus = "OPEN" | "TAKEN" | "CLOSED" | "SKIPPED" | "EXPIRED";

export type Signal = {
  id: number;
  generated_at: string;
  code: string;
  name?: string | null;
  market?: string | null;
  side: Side;
  signal_types?: string | null;   // JSON 文字列
  score?: number | null;
  entry_price?: number | null;
  stop_price?: number | null;
  target_price?: number | null;
  risk?: number | null;
  entry_kind?: string | null;
  order_type?: string | null;
  status: SignalStatus;
  realized_r?: number | null;
  notes?: string | null;
  // 紐付く約定(trades)の集計（建玉状況の表示用）
  filled_shares: number;
  sold_shares: number;
  avg_fill_price?: number | null;
  avg_sell_price?: number | null;
  remaining_shares: number;
  position_value?: number | null;
};

export type SignalFill = {
  shares: number;
  price: number;
  traded_at: string; // YYYY-MM-DD
  fee?: number;
};

export type SignalAttribution = {
  total: number;
  open: number;
  taken: number;
  closed: number;
  skipped: number;
  expired: number;
  take_rate?: number | null;
  live_closed: number;
  live_win_rate?: number | null;
  live_avg_r?: number | null;
  bt_universe?: string | null;
  bt_run_at?: string | null;
  bt_win_rate?: number | null;
  bt_avg_r?: number | null;
  bt_fill_rate?: number | null;
};

export const getSignals = (status?: SignalStatus, limit = 100) =>
  req<Signal[]>(`/api/signals?limit=${limit}${status ? `&status=${status}` : ""}`);

export const setSignalStatus = (id: number, status: SignalStatus) =>
  req<Signal>(`/api/signals/${id}/status`, {
    method: "POST",
    body: JSON.stringify({ status }),
  });

export const signalFill = (id: number, body: SignalFill) =>
  req<Signal>(`/api/signals/${id}/fill`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const signalClose = (id: number, body: SignalFill) =>
  req<Signal>(`/api/signals/${id}/close`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const getSignalTrades = (id: number) =>
  req<Trade[]>(`/api/signals/${id}/trades`);

export const getSignalAttribution = () =>
  req<SignalAttribution>("/api/signals/attribution");

export type ScoreCalibrationBucket = {
  score_lo: number;
  score_hi: number;
  n_signals: number;
  n_entered: number;
  entry_rate?: number | null;
  n_target: number;
  n_stop: number;
  n_timeout: number;
  win_rate?: number | null;
  avg_r?: number | null;
  avg_mfe_r?: number | null;
};

export const getSignalCalibration = () =>
  req<ScoreCalibrationBucket[]>("/api/signals/calibration");

// ── 調整可能パラメータ（設定） ──────────────────────
export type SettingItem = {
  param: string;
  section: string;
  value: number | boolean;
  default: number | boolean;
  overridden: boolean;
};

export const getSettings = () => req<SettingItem[]>("/api/settings");

export const updateSettings = (values: Record<string, number | boolean>) =>
  req<SettingItem[]>("/api/settings", {
    method: "PUT",
    body: JSON.stringify({ values }),
  });

export const resetSetting = (param: string) =>
  req<SettingItem[]>(`/api/settings/${encodeURIComponent(param)}`, {
    method: "DELETE",
  });

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
