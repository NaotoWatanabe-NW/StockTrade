"""
J-Quants 無料版クライアント（業種分類の取得専用）

日本取引所グループ公式の個人向けデータAPI。無料(Free)プランは「12週遅延・直近2年」
のため価格をライブ判断に使うには遅すぎるが、**上場銘柄一覧(/listed/info)に含まれる
業種コード(17/33業種)はほぼ静的**で遅延の影響を受けない。本モジュールはこの業種分類
だけを取得し、トレンド自体は別途 yfinance 価格から合成する設計（data/jquants の責務は
分類のみ）。依存を増やさないため標準ライブラリ urllib だけで実装する。

認証フロー:
  1. リフレッシュトークン（JQUANTS_REFRESH_TOKEN）があればそれを使う。
  2. 無ければ JQUANTS_MAILADDRESS + JQUANTS_PASSWORD で /token/auth_user を叩いて
     refreshToken を取得する。
  3. refreshToken を /token/auth_refresh に渡して idToken（24h有効）を得る。
  4. idToken を Bearer ヘッダに付けて /listed/info を取得する。

資格情報は .env / 環境変数で渡す（config._load_dotenv が読込済み）。
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from typing import Optional

log = logging.getLogger(__name__)

API_BASE = "https://api.jquants.com/v1"
_TIMEOUT_SEC = 30


class JQuantsError(RuntimeError):
    """J-Quants API 関連のエラー（認証失敗・資格情報未設定・HTTP失敗）。"""


def _http_json(method: str, url: str, *, data: Optional[dict] = None,
               headers: Optional[dict] = None) -> dict:
    """JSON を送受信する最小 HTTP ヘルパ（テストではここをモックする）。

    data が指定されれば JSON ボディとして POST する。レスポンスを dict で返す。
    """
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")
        raise JQuantsError(f"J-Quants HTTP {e.code}: {detail[:300]}") from e
    except urllib.error.URLError as e:
        raise JQuantsError(f"J-Quants 接続エラー: {e}") from e


def _refresh_token_from_credentials(mailaddress: str, password: str) -> str:
    """メール+パスワードから refreshToken を取得する。"""
    resp = _http_json(
        "POST", f"{API_BASE}/token/auth_user",
        data={"mailaddress": mailaddress, "password": password},
    )
    token = resp.get("refreshToken")
    if not token:
        raise JQuantsError("auth_user が refreshToken を返しませんでした（資格情報を確認）")
    return token


def get_id_token(
    refresh_token: Optional[str] = None,
    mailaddress: Optional[str] = None,
    password: Optional[str] = None,
) -> str:
    """idToken（API 呼び出し用・24h有効）を取得する。

    引数が None の場合は環境変数（JQUANTS_REFRESH_TOKEN / JQUANTS_MAILADDRESS /
    JQUANTS_PASSWORD）から補完する。資格情報が一切無い場合は JQuantsError。
    """
    refresh_token = refresh_token or os.environ.get("JQUANTS_REFRESH_TOKEN")
    if not refresh_token:
        mailaddress = mailaddress or os.environ.get("JQUANTS_MAILADDRESS")
        password = password or os.environ.get("JQUANTS_PASSWORD")
        if not (mailaddress and password):
            raise JQuantsError(
                "J-Quants の資格情報がありません。.env に JQUANTS_REFRESH_TOKEN、"
                "または JQUANTS_MAILADDRESS と JQUANTS_PASSWORD を設定してください。"
            )
        refresh_token = _refresh_token_from_credentials(mailaddress, password)

    url = f"{API_BASE}/token/auth_refresh?" + urllib.parse.urlencode({"refreshtoken": refresh_token})
    resp = _http_json("POST", url)
    id_token = resp.get("idToken")
    if not id_token:
        raise JQuantsError("auth_refresh が idToken を返しませんでした（refreshToken を確認）")
    return id_token


def _normalize_code(code: str) -> str:
    """J-Quants の5桁コードを yfinance 互換の4桁に正規化する。

    通常株は4桁ティッカー＋末尾1桁（例: 7203 → "72030"）。先頭4文字を採用する
    （新形式の英数字コード "130A0" → "130A" にも対応）。既に4桁ならそのまま。
    """
    code = str(code).strip()
    return code[:4] if len(code) == 5 else code


def _listed_info_to_rows(info_list: list[dict]) -> list[dict]:
    """/listed/info の生レコード列を sectors テーブル用の行に変換する。

    sector_group は合議スコアのグルーピングに使う粗い業種名として17業種名を採用する。
    17業種名が無いレコードは sector_group を None にする（合成インデックスから除外される）。
    """
    rows = []
    for r in info_list:
        sector17_name = r.get("Sector17CodeName") or None
        rows.append({
            "code":          _normalize_code(r.get("Code", "")),
            "name":          r.get("CompanyName") or None,
            "sector17_code": r.get("Sector17Code") or None,
            "sector17_name": sector17_name,
            "sector33_code": r.get("Sector33Code") or None,
            "sector33_name": r.get("Sector33CodeName") or None,
            "sector_group":  sector17_name,
            "market_code":   "JP",
        })
    return rows


def fetch_listed_sectors(id_token: Optional[str] = None) -> list[dict]:
    """上場銘柄一覧から業種分類（17/33業種）を取得して sectors 行のリストで返す。

    id_token を渡さなければ環境変数の資格情報から自動取得する。
    戻り値の各 dict は data.repository.upsert_sectors にそのまま渡せる形。
    """
    token = id_token or get_id_token()
    resp = _http_json("GET", f"{API_BASE}/listed/info",
                      headers={"Authorization": f"Bearer {token}"})
    info_list = resp.get("info", [])
    if not info_list:
        log.warning("J-Quants /listed/info が空のレスポンスを返しました")
    return _listed_info_to_rows(info_list)
