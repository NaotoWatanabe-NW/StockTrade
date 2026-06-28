"""
合成業種インデックスの構築

業種の「トレンド／相対強度」を、価格ソースを追加せずに測るためのモジュール。
ユニバース構成銘柄の日足を業種でグルーピングし、各銘柄の終値を「初値=1.0」に
正規化して等ウェイト平均した **合成業種インデックス**（close 列のみ）を作る。

設計の要点:
  - J-Quants(JP)/yfinance(US) から取った業種分類(code→sector_group)だけを使い、
    トレンドは手元の yfinance 価格から合成する → 12週遅延の影響を受けない。
  - 構成数が min_constituents 未満の業種は作らない（薄い指数は不安定なため）。
  - 各日の値はその日までの価格のみに依存する（未来参照なし）。scoring 側は
    現在バーの日付で `<= t` にスライスして読むので、レジームフィルタと同じ
    ルックアヘッド回避が成り立つ。

価格取得には依存しない純粋関数（テスト容易）。データ取得は呼び出し元が行う。
"""

from __future__ import annotations

from typing import Optional

import pandas as pd


def _normalized_close(df: pd.DataFrame) -> Optional[pd.Series]:
    """終値を「最初の有効値=1.0」に正規化した Series を返す。整合用に tz を外す。

    close 列が無い・有効値が無い場合は None。
    """
    if df is None or "close" not in df.columns:
        return None
    s = df["close"].copy()
    s.index = pd.to_datetime(s.index)
    if getattr(s.index, "tz", None) is not None:
        s.index = s.index.tz_localize(None)
    s = s[s.notna()]
    if s.empty:
        return None
    first = s.iloc[0]
    if first == 0 or first != first:  # 0 や NaN は正規化できない
        return None
    return s / first


def build_sector_indices(
    dfs: dict[str, pd.DataFrame],
    code_to_group: dict[str, str],
    min_constituents: int = 3,
) -> dict[str, pd.DataFrame]:
    """業種ごとの合成インデックスを構築して {sector_group: DataFrame(close)} で返す。

    dfs              : {code: OHLCV DataFrame}（生の日足。close 列を持つこと）
    code_to_group    : {code: sector_group}（dfs に無いコードは無視）
    min_constituents : 合成に必要な最低構成銘柄数。未満の業種は生成しない。

    各業種について構成銘柄の正規化終値を外部結合し、欠損を無視して等ウェイト平均する。
    """
    # 業種 → 正規化済み構成銘柄 Series の収集
    by_group: dict[str, dict[str, pd.Series]] = {}
    for code, df in dfs.items():
        group = code_to_group.get(code)
        if not group:
            continue
        s = _normalized_close(df)
        if s is None:
            continue
        by_group.setdefault(group, {})[code] = s

    indices: dict[str, pd.DataFrame] = {}
    for group, members in by_group.items():
        if len(members) < min_constituents:
            continue
        combined = pd.DataFrame(members)  # 列=銘柄、行=日付（外部結合）
        combined = combined.sort_index()
        mean = combined.mean(axis=1)      # 欠損は自動でスキップ（等ウェイト平均）
        mean = mean[mean.notna()]
        if mean.empty:
            continue
        indices[group] = pd.DataFrame({"close": mean})
    return indices


def sector_series_for(
    code: str,
    code_to_group: dict[str, str],
    indices: dict[str, pd.DataFrame],
) -> Optional[pd.DataFrame]:
    """銘柄コードから所属業種の合成インデックス DataFrame を引く（無ければ None）。"""
    group = code_to_group.get(code)
    if not group:
        return None
    return indices.get(group)
