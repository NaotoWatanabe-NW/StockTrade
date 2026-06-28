"""data.jquants（J-Quants 無料版クライアント）のテスト

外部 HTTP のみモックし、コード正規化・レスポンス変換・資格情報未設定エラーを検証する。
ネットワークには一切アクセスしない。
"""

import pytest

from data import jquants
from data.jquants import _normalize_code, _listed_info_to_rows, JQuantsError


class TestNormalizeCode:
    def test_five_digit_common_stock_to_four_digit(self):
        assert _normalize_code("72030") == "7203"

    def test_already_four_digit_unchanged(self):
        assert _normalize_code("7203") == "7203"

    def test_alphanumeric_five_digit_code(self):
        assert _normalize_code("130A0") == "130A"


class TestListedInfoTransform:
    SAMPLE = [
        {
            "Code": "72030", "CompanyName": "トヨタ自動車",
            "Sector17Code": "6", "Sector17CodeName": "自動車・輸送機",
            "Sector33Code": "3700", "Sector33CodeName": "輸送用機器",
        },
        {
            "Code": "83060", "CompanyName": "三菱UFJ",
            "Sector17Code": "10", "Sector17CodeName": "銀行",
            "Sector33Code": "7050", "Sector33CodeName": "銀行業",
        },
    ]

    def test_maps_fields_and_uses_sector17_as_group(self):
        rows = _listed_info_to_rows(self.SAMPLE)
        toyota = rows[0]
        assert toyota["code"] == "7203"
        assert toyota["name"] == "トヨタ自動車"
        assert toyota["sector17_name"] == "自動車・輸送機"
        assert toyota["sector33_name"] == "輸送用機器"
        assert toyota["sector_group"] == "自動車・輸送機"   # 17業種名をグルーピングに使う
        assert toyota["market_code"] == "JP"

    def test_missing_sector17_name_leaves_group_none(self):
        rows = _listed_info_to_rows([{"Code": "99990", "CompanyName": "謎"}])
        assert rows[0]["sector_group"] is None


class TestAuth:
    def test_missing_credentials_raises(self, monkeypatch):
        for k in ("JQUANTS_REFRESH_TOKEN", "JQUANTS_MAILADDRESS", "JQUANTS_PASSWORD"):
            monkeypatch.delenv(k, raising=False)
        with pytest.raises(JQuantsError):
            jquants.get_id_token()

    def test_refresh_token_is_exchanged_for_id_token(self, monkeypatch):
        monkeypatch.setattr(jquants, "_http_json", lambda *a, **k: {"idToken": "ID123"})
        assert jquants.get_id_token(refresh_token="RT") == "ID123"


class TestFetchListedSectors:
    def test_transforms_listed_info_response(self, monkeypatch):
        fake = {"info": [{
            "Code": "72030", "CompanyName": "トヨタ自動車",
            "Sector17Code": "6", "Sector17CodeName": "自動車・輸送機",
            "Sector33Code": "3700", "Sector33CodeName": "輸送用機器",
        }]}
        monkeypatch.setattr(jquants, "_http_json", lambda *a, **k: fake)
        rows = jquants.fetch_listed_sectors(id_token="dummy")
        assert len(rows) == 1
        assert rows[0]["code"] == "7203"
        assert rows[0]["sector_group"] == "自動車・輸送機"
