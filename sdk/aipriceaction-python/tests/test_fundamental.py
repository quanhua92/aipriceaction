
import pytest
import responses

from aipriceaction import (
    AIPriceAction,
    CompanyInfo,
    FinancialRatios,
)


_BASE = "http://localhost:9000/aipriceaction-archive"


def _make_client(tmp_path):
    return AIPriceAction(
        _BASE,
        cache_dir=str(tmp_path),
        utc_offset=0,
    )


class TestGetCompanyInfo:
    def test_basic(self, mock_s3_fundamental, tmp_path):
        client = _make_client(tmp_path)
        ci = client.get_company_info("ACB", source="vn")
        assert ci is not None
        assert ci.symbol == "ACB"
        assert ci.exchange == "HOSE"
        assert ci.industry == "Ngân hàng"
        assert ci.market_cap == pytest.approx(122252427056200.0)
        assert ci.current_price == pytest.approx(23500.0)
        assert ci.outstanding_shares == 5136656599
        assert len(ci.shareholders) == 2
        assert ci.shareholders[0].name == "Sather Gate Investments Limited"
        assert ci.shareholders[0].percentage == pytest.approx(0.0499)
        assert len(ci.officers) == 2
        assert ci.officers[0].name == "Trần Hùng Huy"
        assert ci.officers[0].position == "Chủ tịch HĐQT"
        assert ci.officers[0].percentage == pytest.approx(0.0343)

    def test_non_bank(self, mock_s3_fundamental, tmp_path):
        client = _make_client(tmp_path)
        ci = client.get_company_info("FPT", source="vn")
        assert ci is not None
        assert ci.symbol == "FPT"
        assert ci.industry == "Công nghệ Thông tin"
        assert ci.current_price == pytest.approx(74000.0)

    def test_not_found(self, mock_s3_fundamental, tmp_path):
        client = _make_client(tmp_path)
        ci = client.get_company_info("VNINDEX", source="vn")
        assert ci is None


class TestGetFinancialRatios:
    def test_basic(self, mock_s3_fundamental, tmp_path):
        client = _make_client(tmp_path)
        fr = client.get_financial_ratios("ACB", source="vn")
        assert fr is not None
        assert fr.ticker == "ACB"
        assert fr.count == 3
        assert len(fr.ratios) == 3

        latest = fr.ratios[0]
        assert latest.year_report == 2025
        assert latest.length_report == 5
        assert latest.pe == pytest.approx(8.2386621048)
        assert latest.pb == pytest.approx(1.3531858822)
        assert latest.roe == pytest.approx(0.1755767655)
        assert latest.roa == pytest.approx(0.0165353343)
        assert latest.npl == pytest.approx(0.0097140463)
        assert latest.car == pytest.approx(0.1245)
        assert latest.casa_ratio == pytest.approx(0.2181753594)

    def test_non_bank(self, mock_s3_fundamental, tmp_path):
        client = _make_client(tmp_path)
        fr = client.get_financial_ratios("FPT", source="vn")
        assert fr is not None
        latest = fr.ratios[0]
        assert latest.pe == pytest.approx(13.0086870277)
        assert latest.roe == pytest.approx(0.2829368545)
        assert latest.current_ratio == pytest.approx(1.400061121)
        assert latest.cash_cycle == pytest.approx(117.9190455063)

    def test_not_found(self, mock_s3_fundamental, tmp_path):
        client = _make_client(tmp_path)
        fr = client.get_financial_ratios("VNINDEX", source="vn")
        assert fr is None


class TestFundamentalHashDedup:
    def test_cache_hit_no_second_get(self, mock_s3_fundamental, tmp_path):
        client = _make_client(tmp_path)
        ci1 = client.get_company_info("ACB", source="vn")
        assert ci1 is not None

        get_calls_after_first = sum(
            1 for c in responses.mock.calls
            if c.request.method == "GET" and "vn.zip" in c.request.url
        )

        ci2 = client.get_company_info("ACB", source="vn")
        assert ci2 is not None
        assert ci2.symbol == "ACB"

        get_calls_after_second = sum(
            1 for c in responses.mock.calls
            if c.request.method == "GET" and "vn.zip" in c.request.url
        )
        assert get_calls_after_second == get_calls_after_first


class TestFundamental404Cache:
    def test_cached_empty(self, mock_s3_fundamental, tmp_path):
        client = _make_client(tmp_path)
        assert client.get_company_info("VNINDEX", source="vn") is None

        calls_before = len(responses.mock.calls)
        assert client.get_company_info("VNINDEX", source="vn") is None
        assert len(responses.mock.calls) == calls_before


class TestExtraFields:
    def test_legacy_fields_in_extra(self, mock_s3_fundamental, tmp_path):
        client = _make_client(tmp_path)
        fr = client.get_financial_ratios("ACB", source="vn")
        assert fr is not None

        legacy = [r for r in fr.ratios if r.year_report == 2013 and r.length_report == 1][0]
        assert legacy.pe == pytest.approx(36.81)
        assert "BSA1" in legacy.extra
        assert "BSA2" in legacy.extra
        assert legacy.extra["BSA2"] == 5806521000000
        assert "eps" in legacy.extra
        assert legacy.extra["eps"] == pytest.approx(333.179)
        assert "revenue" in legacy.extra
        assert "netProfit" in legacy.extra


class TestGetFundamental:
    def test_both(self, mock_s3_fundamental, tmp_path):
        client = _make_client(tmp_path)
        ci, fr = client.get_fundamental("ACB", source="vn")
        assert ci is not None
        assert fr is not None
        assert ci.symbol == "ACB"
        assert fr.ticker == "ACB"

    def test_none_for_missing(self, mock_s3_fundamental, tmp_path):
        client = _make_client(tmp_path)
        ci, fr = client.get_fundamental("VNINDEX", source="vn")
        assert ci is None
        assert fr is None


class TestRoundtrip:
    def test_company_info_to_dict(self, mock_s3_fundamental, tmp_path):
        client = _make_client(tmp_path)
        ci = client.get_company_info("ACB", source="vn")
        assert ci is not None
        d = ci.to_dict()
        assert d["symbol"] == "ACB"
        assert d["exchange"] == "HOSE"
        assert len(d["shareholders"]) == 2
        assert d["shareholders"][0]["name"] == "Sather Gate Investments Limited"
        assert d["officers"][0]["position"] == "Chủ tịch HĐQT"

        ci2 = CompanyInfo.from_dict(d)
        assert ci2.symbol == ci.symbol
        assert ci2.exchange == ci.exchange
        assert len(ci2.shareholders) == len(ci.shareholders)

    def test_financial_ratios_to_dict(self, mock_s3_fundamental, tmp_path):
        client = _make_client(tmp_path)
        fr = client.get_financial_ratios("ACB", source="vn")
        assert fr is not None
        d = fr.to_dict()
        assert d["ticker"] == "ACB"
        assert d["count"] == 3
        assert len(d["ratios"]) == 3
        assert d["ratios"][0]["pe"] == pytest.approx(8.2386621048)

        fr2 = FinancialRatios.from_dict(d)
        assert fr2.ticker == fr.ticker
        assert len(fr2.ratios) == len(fr.ratios)
        assert fr2.ratios[0].pe == pytest.approx(fr.ratios[0].pe)

    def test_ratio_entry_extra_roundtrip(self, mock_s3_fundamental, tmp_path):
        client = _make_client(tmp_path)
        fr = client.get_financial_ratios("ACB", source="vn")
        assert fr is not None
        legacy = [r for r in fr.ratios if r.year_report == 2013 and r.length_report == 1][0]
        d = legacy.to_dict()
        assert "BSA1" in d
        assert "BSA2" in d
        assert d["BSA2"] == 5806521000000
        assert d["pe"] == pytest.approx(36.81)


_PROD = "https://s3.aipriceaction.com"


class TestRealS3:
    def test_acb_company_info(self, tmp_path):
        client = AIPriceAction(_PROD, cache_dir=str(tmp_path), utc_offset=0)
        ci = client.get_company_info("ACB", source="vn")
        assert ci is not None
        assert ci.symbol == "ACB"
        assert ci.industry is not None
        assert ci.outstanding_shares is not None
        assert ci.outstanding_shares > 0
        assert len(ci.shareholders) > 0
        assert len(ci.officers) > 0

    def test_acb_financial_ratios(self, tmp_path):
        client = AIPriceAction(_PROD, cache_dir=str(tmp_path), utc_offset=0)
        fr = client.get_financial_ratios("ACB", source="vn")
        assert fr is not None
        assert fr.ticker == "ACB"
        assert fr.count > 0
        assert len(fr.ratios) == fr.count
        latest = fr.ratios[0]
        assert latest.pe is not None and latest.pe > 0
        assert latest.pb is not None and latest.pb > 0
        assert latest.roe is not None and latest.roe > 0

    def test_fpt_financial_ratios(self, tmp_path):
        client = AIPriceAction(_PROD, cache_dir=str(tmp_path), utc_offset=0)
        fr = client.get_financial_ratios("FPT", source="vn")
        assert fr is not None
        latest = fr.ratios[0]
        assert latest.current_ratio is not None and latest.current_ratio > 0

    def test_fundamental_roundtrip_real(self, tmp_path):
        client = AIPriceAction(_PROD, cache_dir=str(tmp_path), utc_offset=0)
        ci, fr = client.get_fundamental("ACB", source="vn")
        assert ci is not None
        assert fr is not None

        ci_d = ci.to_dict()
        ci2 = CompanyInfo.from_dict(ci_d)
        assert ci2.symbol == ci.symbol
        assert len(ci2.shareholders) == len(ci.shareholders)

        fr_d = fr.to_dict()
        fr2 = FinancialRatios.from_dict(fr_d)
        assert fr2.ticker == fr.ticker
        assert len(fr2.ratios) == len(fr.ratios)
