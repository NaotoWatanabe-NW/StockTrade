"use client";

import Link from "next/link";
import { Fragment, useEffect, useRef, useState } from "react";
import {
  BacktestRun,
  BacktestRunRequest,
  getBacktestRuns,
  getBacktestDefaults,
  getBacktestRun,
  runBacktest,
} from "@/lib/api";

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

const STATUS_LABEL: Record<string, string> = {
  running: "実行中…",
  done: "完了",
  error: "失敗",
};

// 調整可能パラメータの日本語ラベル（未知キーはそのまま表示）
const PARAM_LABELS: Record<string, string> = {
  atr_entry_pullback: "押し目の深さ(ATR)",
  atr_stop_mult: "損切り幅(ATR)",
  reward_risk_ratio: "リワード/リスク比",
  trail_atr_mult: "トレーリング幅(ATR)",
  partial_tp_r: "第1利確のR地点",
  partial_tp_pct: "第1利確の割合",
  move_to_breakeven: "建値ストップへ移動",
  min_abs_score: "最小スコア絶対値",
  breakout_lookback: "ブレイク判定期間",
  ma_short: "短期MA",
  ma_long: "長期MA",
  rsi_period: "RSI期間",
  rsi_oversold: "RSI売られすぎ",
  rsi_overbought: "RSI買われすぎ",
  volume_spike_ratio: "出来高急増倍率",
  weekly_trend_filter: "週足トレンドフィルタ",
  adx_min: "ADX下限",
  index_ma: "指数MA期間",
};

// ── 新規バックテスト実行フォーム ──────────────────────────────
function RunForm({ onStarted }: { onStarted: (run: BacktestRun) => void }) {
  const [defaults, setDefaults] = useState<Record<string, number | boolean> | null>(null);
  const [universe, setUniverse] = useState("JP");
  const [regime, setRegime] = useState(true);
  const [showParams, setShowParams] = useState(false);
  const [vals, setVals] = useState<Record<string, string | boolean>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    getBacktestDefaults()
      .then((d) => {
        setDefaults(d);
        const init: Record<string, string | boolean> = {};
        for (const [k, v] of Object.entries(d)) {
          init[k] = typeof v === "boolean" ? v : String(v);
        }
        setVals(init);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (!defaults) return;

    // フォーム値を型付きパラメータに変換（boolはそのまま、数値はNumber）
    const params: Record<string, number | boolean> = {};
    for (const [k, def] of Object.entries(defaults)) {
      const v = vals[k];
      if (typeof def === "boolean") {
        params[k] = Boolean(v);
      } else {
        const n = Number(v);
        if (!Number.isFinite(n)) {
          setError(`${PARAM_LABELS[k] ?? k} は数値で入力してください`);
          return;
        }
        params[k] = n;
      }
    }

    const body: BacktestRunRequest = {
      universe,
      regime,
      no_partial_tp: false,
      min_score: null,
      params,
    };
    setBusy(true);
    try {
      const run = await runBacktest(body);
      onStarted(run);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel" style={{ marginBottom: 20 }}>
      <h2>新規バックテストを実行</h2>
      <form onSubmit={submit}>
        <div className="row" style={{ gap: 16, alignItems: "center" }}>
          <label>
            対象ユニバース
            <select value={universe} onChange={(e) => setUniverse(e.target.value)}>
              <option value="JP">日本株 (JP)</option>
              <option value="US">米国株 (US)</option>
              <option value="ALL">すべて (ALL)</option>
            </select>
          </label>
          <label className="row" style={{ gap: 6, alignItems: "center" }}>
            <input
              type="checkbox"
              checked={regime}
              onChange={(e) => setRegime(e.target.checked)}
            />
            レジームフィルタを有効化
          </label>
          <button
            type="button"
            className="ghost"
            style={{ fontSize: 13, padding: "4px 10px" }}
            onClick={() => setShowParams((s) => !s)}
          >
            {showParams ? "▲ パラメータを隠す" : "▼ パラメータを調整"}
          </button>
          <button type="submit" disabled={busy || !defaults}>
            {busy ? "実行中…" : "実行する"}
          </button>
        </div>

        {showParams && defaults && (
          <div
            style={{
              marginTop: 14,
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
              gap: 10,
            }}
          >
            {Object.entries(defaults).map(([k, def]) => (
              <label key={k} style={{ fontSize: 13 }}>
                {PARAM_LABELS[k] ?? k}
                {typeof def === "boolean" ? (
                  <input
                    type="checkbox"
                    checked={Boolean(vals[k])}
                    onChange={(e) => setVals({ ...vals, [k]: e.target.checked })}
                    style={{ marginLeft: 8 }}
                  />
                ) : (
                  <input
                    value={String(vals[k] ?? "")}
                    inputMode="decimal"
                    onChange={(e) => setVals({ ...vals, [k]: e.target.value })}
                    style={{ width: "100%" }}
                  />
                )}
              </label>
            ))}
          </div>
        )}
      </form>
      {error && <div className="error">{error}</div>}
      <p className="muted" style={{ fontSize: 12, marginTop: 10, marginBottom: 0 }}>
        Web実行はDBキャッシュを使います。未キャッシュ銘柄が多い初回は時間がかかります
        （<code>python -m backtest.runner --universe JP --save</code> で事前ウォームアップ推奨）。
      </p>
    </div>
  );
}

export default function BacktestPage() {
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);
  const [pollingId, setPollingId] = useState<number | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = () =>
    getBacktestRuns(30)
      .then(setRuns)
      .catch((e) => setError(String(e)));

  useEffect(() => {
    load();
  }, []);

  // 実行中ジョブを 2 秒間隔でポーリングし、done/error になったら一覧を更新して停止
  useEffect(() => {
    if (pollingId == null) return;
    timer.current = setInterval(async () => {
      try {
        const run = await getBacktestRun(pollingId);
        if (run.status !== "running") {
          setPollingId(null);
          load();
        } else {
          load();
        }
      } catch {
        setPollingId(null);
      }
    }, 2000);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [pollingId]);

  const onStarted = (run: BacktestRun) => {
    setRuns((prev) => [run, ...prev]);
    setPollingId(run.id);
  };

  return (
    <div>
      <h1>バックテスト</h1>
      <p className="muted" style={{ marginBottom: 16 }}>
        パラメータを指定してWeb上で実行し、結果をここに蓄積・比較します。
      </p>

      {error && <div className="error">{error}</div>}

      <RunForm onStarted={onStarted} />

      {runs.length === 0 && !error && (
        <div className="panel muted">バックテスト結果がまだありません。</div>
      )}

      {runs.length > 0 && (
        <div className="panel" style={{ overflowX: "auto" }}>
          <table>
            <thead>
              <tr>
                <th>実行日時</th>
                <th>状態</th>
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
                <Fragment key={r.id}>
                  <tr className={r.status === "done" ? rowColor(r.win_rate) : ""}>
                    <td className="muted">
                      <Link href={`/backtest/${r.id}`} style={{ textDecoration: "none" }}>
                        {r.run_at.slice(0, 16)}
                      </Link>
                    </td>
                    <td
                      className={
                        r.status === "error"
                          ? "neg"
                          : r.status === "running"
                          ? "muted"
                          : "pos"
                      }
                      title={r.error ?? undefined}
                    >
                      {STATUS_LABEL[r.status] ?? r.status}
                    </td>
                    <td>{r.universe}</td>
                    <td className="num">{r.n_signals ?? "-"}</td>
                    <td className="num">{pct(r.fill_rate)}</td>
                    <td className={`num ${rowColor(r.win_rate)}`}>{pct(r.win_rate)}</td>
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
                        {r.status === "done" && (
                          <Link href={`/backtest/${r.id}`}>
                            <button className="ghost" style={{ fontSize: 12, padding: "2px 8px" }}>
                              資産曲線
                            </button>
                          </Link>
                        )}
                        {r.params && (
                          <button
                            className="ghost"
                            style={{ fontSize: 12, padding: "2px 8px" }}
                            onClick={() => setExpanded(expanded === r.id ? null : r.id)}
                          >
                            {expanded === r.id ? "▲" : "▼"} params
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                  {expanded === r.id && r.params && (
                    <tr>
                      <td colSpan={12}>
                        {r.status === "error" && r.error && (
                          <div className="error" style={{ marginBottom: 8 }}>{r.error}</div>
                        )}
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
                </Fragment>
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
