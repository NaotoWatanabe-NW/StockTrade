"use client";

import { Fragment, useEffect, useState } from "react";
import {
  Signal,
  SignalStatus,
  SignalAttribution,
  ScoreCalibrationBucket,
  Trade,
  SizingResponse,
  getSignals,
  setSignalStatus,
  getSignalAttribution,
  getSignalCalibration,
  getSizingSuggestions,
  signalFill,
  signalClose,
  getSignalTrades,
  deleteTrade,
  fmtPrice,
} from "@/lib/api";

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

const STATUS_LABEL: Record<SignalStatus, string> = {
  OPEN: "未対応",
  TAKEN: "建玉中",
  CLOSED: "決済済",
  SKIPPED: "見送り",
  EXPIRED: "期限切れ",
};

const STATUS_CLASS: Record<SignalStatus, string> = {
  OPEN: "",
  TAKEN: "pos",
  CLOSED: "",
  SKIPPED: "muted",
  EXPIRED: "muted",
};

function pct(v: number | null | undefined) {
  return v == null ? "-" : `${(v * 100).toFixed(1)}%`;
}
function r3(v: number | null | undefined) {
  if (v == null) return "-";
  return v >= 0 ? `+${v.toFixed(3)}R` : `${v.toFixed(3)}R`;
}

function parseTypes(json: string | null | undefined): string[] {
  if (!json) return [];
  try {
    return JSON.parse(json);
  } catch {
    return [];
  }
}

// ── ライブ vs バックテスト 比較カード ────────────────────────
function AttributionCard({ a }: { a: SignalAttribution }) {
  const liveR = a.live_avg_r;
  const btR = a.bt_avg_r;
  // ライブが期待値を上回っていれば pos、下回れば neg
  const verdictClass =
    liveR == null || btR == null ? "" : liveR >= btR ? "pos" : "neg";

  return (
    <div className="panel" style={{ marginBottom: 20 }}>
      <h2>ライブ成績 vs バックテスト期待値</h2>
      <p className="muted" style={{ marginTop: -8, marginBottom: 12 }}>
        記録シグナル {a.total} 件（未対応 {a.open} / 建玉中 {a.taken} / 決済済{" "}
        {a.closed} / 見送り {a.skipped} / 期限切れ {a.expired}）
      </p>
      <div className="cards">
        <div className="panel card">
          <div className="label">約定到達率</div>
          <div className="value">{pct(a.take_rate)}</div>
          <div className="muted">終局シグナルのうち約定に至った割合</div>
        </div>
        <div className="panel card">
          <div className="label">ライブ勝率</div>
          <div className="value">{pct(a.live_win_rate)}</div>
          <div className="muted">
            バックテスト期待 {pct(a.bt_win_rate)}（{a.live_closed} 件決済）
          </div>
        </div>
        <div className="panel card">
          <div className="label">ライブ平均R</div>
          <div className={`value ${verdictClass}`}>{r3(liveR)}</div>
          <div className="muted">バックテスト期待 {r3(btR)}</div>
        </div>
        <div className="panel card">
          <div className="label">期待値との差</div>
          <div className={`value ${verdictClass}`}>
            {liveR != null && btR != null ? r3(liveR - btR) : "-"}
          </div>
          <div className="muted">
            {liveR == null
              ? "決済データ待ち"
              : verdictClass === "pos"
              ? "期待を上回っています"
              : "期待を下回っています"}
          </div>
        </div>
      </div>
      {a.live_closed === 0 && (
        <p className="muted" style={{ marginTop: 12 }}>
          まだ決済済みの取引がありません。シグナルを「建玉中」にして取引記録で
          <code>signal_id</code> を紐付けると、決済後に実現Rが自動計算されます。
        </p>
      )}
    </div>
  );
}

// ── スコア・キャリブレーション（予測 vs 実勢価格） ──────────────
function CalibrationCard({ rows }: { rows: ScoreCalibrationBucket[] }) {
  const hasData = rows.some((b) => b.n_signals > 0);
  return (
    <div className="panel" style={{ marginBottom: 20 }}>
      <h2>スコア別の予測的中度（キャリブレーション）</h2>
      <p className="muted" style={{ marginTop: -8, marginBottom: 12 }}>
        記録済みシグナルの計画（指値/損切り/利確）が実勢価格でどう決着したかの集計。
        スコアが高い帯ほど勝率・期待Rが高ければ、スコアが予測力を持っている証拠です。
        取引したかに依存しません（<code>python -m scripts.evaluate_signal_outcomes</code> で更新）。
      </p>
      {!hasData ? (
        <p className="muted">
          まだ評価済みのシグナルがありません。シグナル記録後に
          <code>python -m scripts.evaluate_signal_outcomes</code> を実行してください。
        </p>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table>
            <thead>
              <tr>
                <th>スコア帯</th>
                <th className="num">件数</th>
                <th className="num">約定率</th>
                <th className="num">利確</th>
                <th className="num">損切</th>
                <th className="num">時間切れ</th>
                <th className="num">勝率</th>
                <th className="num">平均R</th>
                <th className="num">平均最大含み益</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((b) => (
                <tr key={`${b.score_lo}-${b.score_hi}`}>
                  <td>{b.score_lo}–{b.score_hi}</td>
                  <td className="num">{b.n_signals}</td>
                  <td className="num">{pct(b.entry_rate)}</td>
                  <td className="num pos">{b.n_target}</td>
                  <td className="num neg">{b.n_stop}</td>
                  <td className="num muted">{b.n_timeout}</td>
                  <td className="num">{pct(b.win_rate)}</td>
                  <td
                    className={`num ${
                      b.avg_r == null ? "" : b.avg_r >= 0 ? "pos" : "neg"
                    }`}
                  >
                    {r3(b.avg_r)}
                  </td>
                  <td className="num">{r3(b.avg_mfe_r)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── 推奨サイジング（口座残高→何株買うか） ──────────────────────
function SizingCard({ s }: { s: SizingResponse }) {
  const yen = (v: number) => `¥${Math.round(v).toLocaleString()}`;
  return (
    <div className="panel" style={{ marginBottom: 20 }}>
      <h2>推奨サイジング（口座残高→株数）</h2>
      <p className="muted" style={{ marginTop: -8, marginBottom: 12 }}>
        口座サイズ {yen(s.account_size)} ・ 1トレード許容リスク {s.risk_per_trade_pct}% ・
        保有 {s.open_positions}/{s.max_positions}（残り {s.remaining_slots} 枠 / 熱量 {s.heat_pct.toFixed(1)}%）。
        値は<a href="/settings">設定</a>の口座サイズで調整できます。
      </p>
      {s.suggestions.length === 0 ? (
        <p className="muted">
          推奨対象（OPEN の買いシグナル）がありません。
        </p>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table>
            <thead>
              <tr>
                <th>銘柄</th>
                <th className="num">スコア</th>
                <th className="num">指値</th>
                <th className="num">損切り</th>
                <th className="num">推奨株数</th>
                <th className="num">投資額</th>
                <th className="num">実リスク額</th>
              </tr>
            </thead>
            <tbody>
              {s.suggestions.map((g) => (
                <tr key={g.signal_id}>
                  <td>
                    {g.name ?? g.code}
                    <span className="muted"> {g.code}</span>
                  </td>
                  <td className="num">{g.score != null ? g.score.toFixed(0) : "-"}</td>
                  <td className="num">{fmtPrice(g.code, g.entry_price)}</td>
                  <td className="num neg">{fmtPrice(g.code, g.stop_price)}</td>
                  <td className="num pos">{g.suggested_shares.toLocaleString()}株</td>
                  <td className="num">{fmtPrice(g.code, g.investment)}</td>
                  <td className="num neg">{fmtPrice(g.code, g.risk_amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── 約定/決済の入力フォーム（シグナルに紐付く trades を作成） ──────
function FillForm({
  signalId,
  kind,
  label,
  onDone,
}: {
  signalId: number;
  kind: "fill" | "close";
  label: string;
  onDone: () => void;
}) {
  const [shares, setShares] = useState("");
  const [price, setPrice] = useState("");
  const [date, setDate] = useState(today());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr("");
    if (shares === "" || price === "") {
      setErr("株数・単価は必須です");
      return;
    }
    setBusy(true);
    try {
      const body = { shares: Number(shares), price: Number(price), traded_at: date };
      if (kind === "fill") await signalFill(signalId, body);
      else await signalClose(signalId, body);
      setShares("");
      setPrice("");
      onDone();
    } catch (e2) {
      setErr(String(e2));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="row" style={{ gap: 6, alignItems: "flex-end" }}>
      <strong style={{ fontSize: 12, width: 84 }}>{label}</strong>
      <label style={{ fontSize: 12 }}>
        株数
        <input
          value={shares}
          inputMode="decimal"
          onChange={(e) => setShares(e.target.value)}
          style={{ width: 70 }}
        />
      </label>
      <label style={{ fontSize: 12 }}>
        単価
        <input
          value={price}
          inputMode="decimal"
          onChange={(e) => setPrice(e.target.value)}
          style={{ width: 84 }}
        />
      </label>
      <label style={{ fontSize: 12 }}>
        約定日
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
      </label>
      <button type="submit" disabled={busy} style={{ fontSize: 12, padding: "4px 10px" }}>
        記録
      </button>
      {err && (
        <span className="error" style={{ marginTop: 0, padding: "2px 8px" }}>
          {err}
        </span>
      )}
    </form>
  );
}

// ── シグナル行の展開パネル（約定/決済・状態操作・紐付き約定） ──────
function SignalDetail({ signal, onChange }: { signal: Signal; onChange: () => void }) {
  const [trades, setTrades] = useState<Trade[]>([]);
  const isBuy = signal.side === "BUY";

  const loadTrades = () =>
    getSignalTrades(signal.id)
      .then(setTrades)
      .catch(() => setTrades([]));

  useEffect(() => {
    loadTrades();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signal.id]);

  const refresh = () => {
    onChange();
    loadTrades();
  };

  const changeStatus = async (status: SignalStatus) => {
    await setSignalStatus(signal.id, status);
    refresh();
  };

  const removeTrade = async (id?: number) => {
    if (id === undefined) return;
    if (!confirm("この約定を削除しますか？")) return;
    await deleteTrade(id);
    refresh();
  };

  return (
    <div style={{ padding: 12, background: "var(--bg2)", borderRadius: 4 }}>
      {isBuy ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <FillForm signalId={signal.id} kind="fill" label="約定を記録" onDone={refresh} />
          <FillForm signalId={signal.id} kind="close" label="決済を記録" onDone={refresh} />
        </div>
      ) : (
        <p className="muted" style={{ fontSize: 12, margin: 0 }}>
          売りシグナルは保有の手仕舞い指示のため、約定/決済の記録対象外です（状態のみ管理）。
        </p>
      )}

      <div className="row" style={{ gap: 6, marginTop: 10, alignItems: "center" }}>
        <span className="muted" style={{ fontSize: 12 }}>状態操作:</span>
        {signal.status === "OPEN" && (
          <>
            <button
              className="ghost"
              style={{ fontSize: 12, padding: "2px 8px" }}
              onClick={() => changeStatus("SKIPPED")}
            >
              見送り
            </button>
            <button
              className="ghost"
              style={{ fontSize: 12, padding: "2px 8px" }}
              onClick={() => changeStatus("EXPIRED")}
            >
              期限切れ
            </button>
          </>
        )}
        {(signal.status === "SKIPPED" || signal.status === "EXPIRED") && (
          <button
            className="ghost"
            style={{ fontSize: 12, padding: "2px 8px" }}
            onClick={() => changeStatus("OPEN")}
          >
            未対応に戻す
          </button>
        )}
        {(signal.status === "TAKEN" || signal.status === "CLOSED") && (
          <span className="muted" style={{ fontSize: 12 }}>
            約定の記録/削除で自動更新されます
          </span>
        )}
      </div>

      {trades.length > 0 && (
        <table style={{ marginTop: 10 }}>
          <thead>
            <tr>
              <th>約定日</th>
              <th>売買</th>
              <th className="num">株数</th>
              <th className="num">単価</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t) => (
              <tr key={t.id}>
                <td>{t.traded_at}</td>
                <td className={t.side === "BUY" ? "pos" : "neg"}>
                  {t.side === "BUY" ? "買い" : "売り"}
                </td>
                <td className="num">{t.shares}</td>
                <td className="num">{fmtPrice(t.code, t.price)}</td>
                <td className="num">
                  <button
                    className="danger"
                    style={{ fontSize: 12, padding: "2px 8px" }}
                    onClick={() => removeTrade(t.id)}
                  >
                    削除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function SignalsPage() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [attr, setAttr] = useState<SignalAttribution | null>(null);
  const [calibration, setCalibration] = useState<ScoreCalibrationBucket[]>([]);
  const [sizing, setSizing] = useState<SizingResponse | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<SignalStatus | "ALL">("ALL");
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = () => {
    Promise.allSettled([
      getSignals(filter === "ALL" ? undefined : filter),
      getSignalAttribution(),
      getSignalCalibration(),
      getSizingSuggestions(),
    ]).then(([s, a, c, z]) => {
      if (s.status === "fulfilled") setSignals(s.value);
      else setError(String(s.reason?.message ?? s.reason));
      if (a.status === "fulfilled") setAttr(a.value);
      if (c.status === "fulfilled") setCalibration(c.value);
      if (z.status === "fulfilled") setSizing(z.value);
    });
  };

  useEffect(load, [filter]);

  return (
    <div>
      <h1>シグナル追跡</h1>
      <p className="muted" style={{ marginBottom: 16 }}>
        スクリーナーが出したシグナルを記録し、実取引の結果と突き合わせて
        戦略がライブでも機能しているか検証します。
      </p>

      {error && <div className="error">{error}</div>}

      {attr && <AttributionCard a={attr} />}

      {sizing && <SizingCard s={sizing} />}

      <CalibrationCard rows={calibration} />

      <div className="panel">
        <div className="row" style={{ marginBottom: 12, gap: 6 }}>
          {(["ALL", "OPEN", "TAKEN", "CLOSED", "SKIPPED", "EXPIRED"] as const).map(
            (f) => (
              <button
                key={f}
                className={filter === f ? "" : "ghost"}
                style={{ fontSize: 13, padding: "4px 10px" }}
                onClick={() => setFilter(f)}
              >
                {f === "ALL" ? "すべて" : STATUS_LABEL[f]}
              </button>
            )
          )}
        </div>

        {signals.length === 0 ? (
          <p className="muted">
            該当するシグナルがありません。<code>python main.py</code>{" "}
            を実行するとシグナルが記録されます。
          </p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th>発生日時</th>
                  <th>銘柄</th>
                  <th>方向</th>
                  <th className="num">スコア</th>
                  <th className="num">指値</th>
                  <th className="num">損切り</th>
                  <th className="num">利確</th>
                  <th>シグナル</th>
                  <th>状態</th>
                  <th className="num">保有株数</th>
                  <th className="num">約定単価</th>
                  <th className="num">実現R</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {signals.map((s) => (
                  <Fragment key={s.id}>
                    <tr>
                      <td className="muted">{s.generated_at.slice(0, 16)}</td>
                      <td>
                        {s.name ?? s.code}
                        <span className="muted"> {s.code}</span>
                      </td>
                      <td className={s.side === "BUY" ? "pos" : "neg"}>{s.side}</td>
                      <td className="num">
                        {s.score != null ? s.score.toFixed(0) : "-"}
                      </td>
                      <td className="num">{fmtPrice(s.code, s.entry_price)}</td>
                      <td className="num neg">{fmtPrice(s.code, s.stop_price)}</td>
                      <td className="num pos">{fmtPrice(s.code, s.target_price)}</td>
                      <td className="muted" style={{ fontSize: 12 }}>
                        {parseTypes(s.signal_types).join(", ")}
                      </td>
                      <td className={STATUS_CLASS[s.status]}>
                        {STATUS_LABEL[s.status]}
                      </td>
                      <td className="num">
                        {s.remaining_shares > 0 ? s.remaining_shares : "-"}
                      </td>
                      <td className="num">
                        {fmtPrice(s.code, s.avg_fill_price)}
                      </td>
                      <td
                        className={`num ${
                          s.realized_r == null
                            ? ""
                            : s.realized_r >= 0
                            ? "pos"
                            : "neg"
                        }`}
                      >
                        {r3(s.realized_r)}
                      </td>
                      <td>
                        <button
                          className="ghost"
                          style={{ fontSize: 12, padding: "2px 8px" }}
                          onClick={() =>
                            setExpanded(expanded === s.id ? null : s.id)
                          }
                        >
                          {expanded === s.id ? "▲ 閉じる" : "▼ 約定/決済"}
                        </button>
                      </td>
                    </tr>
                    {expanded === s.id && (
                      <tr>
                        <td colSpan={13}>
                          <SignalDetail signal={s} onChange={load} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="panel" style={{ marginTop: 20 }}>
        <h2>使い方</h2>
        <ol className="muted" style={{ lineHeight: 1.8, paddingLeft: 20 }}>
          <li><code>python main.py</code> でスクリーニングするとシグナルが自動記録されます。</li>
          <li>エントリーしたら行末の「▼ 約定/決済」を開き、<strong>約定を記録</strong>（株数・単価）すると自動で「建玉中」になります。記録した約定は取引記録・損益にも反映されます。</li>
          <li>見送った/期限切れの場合は同じパネルの状態操作で切り替えます。</li>
          <li>決済（売却）を記録すると全株決済で自動「決済済」になり、実現Rが計算されて上のカードでバックテスト期待値と比較できます。</li>
        </ol>
      </div>
    </div>
  );
}
