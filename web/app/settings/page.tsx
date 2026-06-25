"use client";

import { useEffect, useState } from "react";
import {
  SettingItem,
  getSettings,
  updateSettings,
  resetSetting,
} from "@/lib/api";

const SECTION_LABELS: Record<string, string> = {
  SCREENING_CONFIG: "スクリーニング（シグナル検出）",
  SCORING_CONFIG: "スコアリング",
  TRADE_PLAN_CONFIG: "注文プラン（ATR基準の価格）",
  EXIT_CONFIG: "出口・リスク管理",
  REGIME_CONFIG: "レジームフィルタ",
  RISK_CONFIG: "資金管理（口座サイズ・サイジング）",
};

const SECTION_ORDER = [
  "RISK_CONFIG",
  "SCREENING_CONFIG",
  "SCORING_CONFIG",
  "TRADE_PLAN_CONFIG",
  "EXIT_CONFIG",
  "REGIME_CONFIG",
];

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
  account_size: "口座サイズ",
  risk_per_trade_pct: "1トレード許容リスク(%)",
  max_positions: "同時保有上限",
};

export default function SettingsPage() {
  const [items, setItems] = useState<SettingItem[]>([]);
  const [form, setForm] = useState<Record<string, string | boolean>>({});
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const apply = (data: SettingItem[]) => {
    setItems(data);
    const f: Record<string, string | boolean> = {};
    for (const it of data) {
      f[it.param] = typeof it.value === "boolean" ? it.value : String(it.value);
    }
    setForm(f);
  };

  const load = () =>
    getSettings()
      .then(apply)
      .catch((e) => setError(String(e)));

  useEffect(() => {
    load();
  }, []);

  // フォーム値が現在の有効値と異なるものだけを抽出
  const changed = (): Record<string, number | boolean> => {
    const out: Record<string, number | boolean> = {};
    for (const it of items) {
      const v = form[it.param];
      if (typeof it.value === "boolean") {
        if (Boolean(v) !== it.value) out[it.param] = Boolean(v);
      } else {
        const n = Number(v);
        if (Number.isFinite(n) && n !== it.value) out[it.param] = n;
      }
    }
    return out;
  };

  const save = async () => {
    setError("");
    setMsg("");
    const values = changed();
    if (Object.keys(values).length === 0) {
      setMsg("変更はありません。");
      return;
    }
    setBusy(true);
    try {
      const data = await updateSettings(values);
      apply(data);
      setMsg(`${Object.keys(values).length}件のパラメータを保存しました（次回スキャン/バックテストに反映）。`);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const reset = async (param: string) => {
    setError("");
    setMsg("");
    try {
      apply(await resetSetting(param));
    } catch (e) {
      setError(String(e));
    }
  };

  const dirty = Object.keys(changed()).length;
  const bySection = (section: string) => items.filter((i) => i.section === section);

  return (
    <div>
      <h1>パラメータ設定</h1>
      <p className="muted" style={{ marginBottom: 16 }}>
        売買シグナル検出・資金管理のしきい値をここで調整します。保存すると
        <strong>次回のスキャンとバックテストに即反映</strong>されます（再起動不要）。
      </p>

      {error && <div className="error">{error}</div>}
      {msg && (
        <div className="panel" style={{ borderColor: "var(--pos)", marginBottom: 16 }}>
          {msg}
        </div>
      )}

      <div className="row" style={{ gap: 8, marginBottom: 16, alignItems: "center" }}>
        <button onClick={save} disabled={busy || dirty === 0}>
          {busy ? "保存中…" : dirty > 0 ? `保存（${dirty}件）` : "変更なし"}
        </button>
        <button className="ghost" onClick={load} disabled={busy}>
          編集を破棄
        </button>
      </div>

      {SECTION_ORDER.filter((s) => bySection(s).length > 0).map((section) => (
        <div className="panel" key={section} style={{ marginBottom: 16 }}>
          <h2>{SECTION_LABELS[section] ?? section}</h2>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
              gap: 12,
            }}
          >
            {bySection(section).map((it) => (
              <div key={it.param}>
                <label style={{ fontSize: 13 }}>
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <span>
                      {PARAM_LABELS[it.param] ?? it.param}
                      {it.overridden && (
                        <span className="pos" style={{ fontSize: 11, marginLeft: 6 }}>
                          ●変更中
                        </span>
                      )}
                    </span>
                  </div>
                  {typeof it.value === "boolean" ? (
                    <input
                      type="checkbox"
                      checked={Boolean(form[it.param])}
                      onChange={(e) =>
                        setForm({ ...form, [it.param]: e.target.checked })
                      }
                    />
                  ) : (
                    <input
                      value={String(form[it.param] ?? "")}
                      inputMode="decimal"
                      onChange={(e) =>
                        setForm({ ...form, [it.param]: e.target.value })
                      }
                      style={{ width: "100%" }}
                    />
                  )}
                </label>
                <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
                  既定: {String(it.default)}
                  {it.overridden && (
                    <button
                      className="ghost"
                      style={{ fontSize: 11, padding: "1px 6px", marginLeft: 8 }}
                      onClick={() => reset(it.param)}
                    >
                      既定に戻す
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
