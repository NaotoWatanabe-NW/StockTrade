"use client";

import { useEffect, useState } from "react";
import {
  Signal,
  SignalStatus,
  SignalAttribution,
  getSignals,
  setSignalStatus,
  getSignalAttribution,
  fmtPrice,
} from "@/lib/api";

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

export default function SignalsPage() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [attr, setAttr] = useState<SignalAttribution | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<SignalStatus | "ALL">("ALL");

  const load = () => {
    Promise.allSettled([
      getSignals(filter === "ALL" ? undefined : filter),
      getSignalAttribution(),
    ]).then(([s, a]) => {
      if (s.status === "fulfilled") setSignals(s.value);
      else setError(String(s.reason?.message ?? s.reason));
      if (a.status === "fulfilled") setAttr(a.value);
    });
  };

  useEffect(load, [filter]);

  const onStatus = async (id: number, status: SignalStatus) => {
    try {
      await setSignalStatus(id, status);
      load();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div>
      <h1>シグナル追跡</h1>
      <p className="muted" style={{ marginBottom: 16 }}>
        スクリーナーが出したシグナルを記録し、実取引の結果と突き合わせて
        戦略がライブでも機能しているか検証します。
      </p>

      {error && <div className="error">{error}</div>}

      {attr && <AttributionCard a={attr} />}

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
                  <th className="num">実現R</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {signals.map((s) => (
                  <tr key={s.id}>
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
                      {(s.status === "OPEN" || s.status === "EXPIRED") && (
                        <div className="row" style={{ gap: 4 }}>
                          <button
                            className="ghost"
                            style={{ fontSize: 12, padding: "2px 8px" }}
                            onClick={() => onStatus(s.id, "TAKEN")}
                          >
                            建玉中に
                          </button>
                          <button
                            className="ghost"
                            style={{ fontSize: 12, padding: "2px 8px" }}
                            onClick={() => onStatus(s.id, "SKIPPED")}
                          >
                            見送り
                          </button>
                        </div>
                      )}
                      {s.status === "SKIPPED" && (
                        <button
                          className="ghost"
                          style={{ fontSize: 12, padding: "2px 8px" }}
                          onClick={() => onStatus(s.id, "OPEN")}
                        >
                          戻す
                        </button>
                      )}
                    </td>
                  </tr>
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
          <li>実際にエントリーしたら「建玉中に」を押し、取引記録でその約定を <code>signal_id</code> で紐付けます。</li>
          <li>見送った場合は「見送り」を押します。</li>
          <li>決済（売却）を記録すると実現Rが自動計算され、上のカードでバックテスト期待値と比較できます。</li>
        </ol>
      </div>
    </div>
  );
}
