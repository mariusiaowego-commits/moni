"""tests/test_case_geography_normalize.py — sprint-19 normalize_geo."""

from __future__ import annotations

from moni.cases.normalize import normalize_geo, normalize_geos


class TestAliasZhEn:
    def test_alias_zh_en(self):
        for tok in ("美利坚", "USA", "America", "美国", "US"):
            g = normalize_geo(tok)
            assert g is not None, tok
            assert g["country_code"] == "US"
            assert g["region"] == "NAM"
            assert g["country_name"] == "美国"

    def test_alias_global(self):
        for tok in ("BIS", "mBridge", "SWIFT", "全球", "国际清算银行"):
            g = normalize_geo(tok)
            assert g is not None, tok
            assert g["country_code"] == "GLOBAL"
            assert g["region"] == "GLOBAL"


class TestSubjectDefaults:
    def test_subject_geo_defaults(self):
        zhao = normalize_geo(None, subject="招商银行")
        assert zhao["country_code"] == "CN"
        assert zhao["region"] == "APAC"
        jpm = normalize_geo(None, subject="摩根大通")
        assert jpm["country_code"] == "US"
        assert jpm["region"] == "NAM"
        hsbc = normalize_geo(None, subject="汇丰银行")
        assert hsbc["country_code"] == "GB"
        assert hsbc["region"] == "EMEA"
        # 别名主体
        assert normalize_geo(None, subject="招行")["country_code"] == "CN"

    def test_counterparty_fallback(self):
        g = normalize_geo(None, subject="某未知科技公司XYZ", counterparty="摩根大通")
        assert g is not None
        assert g["country_code"] == "US"
        assert g["source_type"] == "auto_rule"


class TestInputCompat:
    def test_input_compat(self):
        assert normalize_geo("中国")["country_code"] == "CN"
        assert normalize_geo(["美国"])["country_code"] == "US"
        assert normalize_geo(None, subject="招商银行")["country_code"] == "CN"
        assert normalize_geo({"country": "新加坡"})["country_code"] == "SG"

    def test_invalid_input(self):
        assert normalize_geo(None) is None
        assert normalize_geo("") is None
        assert normalize_geo("   ") is None
        assert normalize_geo("asdfghqwerty") is None
        assert normalize_geo(None, subject="完全没听过的壳公司") is None


class TestSubRegion:
    def test_sub_region(self):
        assert normalize_geo("日本")["sub_region"] == "东亚"
        assert normalize_geo("新加坡")["sub_region"] == "东南亚"
        assert normalize_geo("英国")["sub_region"] == "西欧"
        assert normalize_geo("全球")["sub_region"] is None


class TestMulti:
    def test_multi_country_primary(self):
        geos = normalize_geos(["中国", "美国"])
        assert [g["country_code"] for g in geos] == ["CN", "US"]
        assert geos[0]["is_primary"] == 1
        assert geos[1]["is_primary"] == 0
        assert geos[0]["source_type"] == "auto_llm"
