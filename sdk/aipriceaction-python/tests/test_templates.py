from aipriceaction.single import get_single_templates, get_single_template, SINGLE_TEMPLATES
from aipriceaction.multi import get_multi_templates, get_multi_template, MULTI_TEMPLATES


class TestSingleTemplates:
    def test_single_en_count(self):
        templates = get_single_templates("en")
        assert len(templates) == 6

    def test_single_vn_count(self):
        templates = get_single_templates("vn")
        assert len(templates) == 6

    def test_single_unknown_lang(self):
        templates = get_single_templates("zz")
        assert templates == []

    def test_single_template_by_index(self):
        template = get_single_template("en", 0)
        assert template is not None
        assert "title" in template
        assert "snippet" in template
        assert "question" in template
        assert template["title"] == "Trading Opportunity"

    def test_single_template_out_of_range(self):
        assert get_single_template("en", -1) is None
        assert get_single_template("en", 100) is None

    def test_single_en_matches_dict(self):
        templates = get_single_templates("en")
        assert templates == SINGLE_TEMPLATES["en"]

    def test_single_vn_matches_dict(self):
        templates = get_single_templates("vn")
        assert templates == SINGLE_TEMPLATES["vn"]


class TestMultiTemplates:
    def test_multi_en_count(self):
        templates = get_multi_templates("en")
        assert len(templates) == 7

    def test_multi_vn_count(self):
        templates = get_multi_templates("vn")
        assert len(templates) == 7

    def test_multi_unknown_lang_fallback(self):
        # Unknown language falls back to English (unlike single)
        templates = get_multi_templates("zz")
        assert templates == MULTI_TEMPLATES["en"]
        assert len(templates) == 7

    def test_multi_template_by_index(self):
        template = get_multi_template("en", 0)
        assert template is not None
        assert "title" in template
        assert "snippet" in template
        assert "question" in template
        assert template["title"] == "Trading Opportunity"

    def test_multi_template_out_of_range(self):
        assert get_multi_template("en", -1) is None
        assert get_multi_template("en", 100) is None

    def test_multi_en_matches_dict(self):
        templates = get_multi_templates("en")
        assert templates == MULTI_TEMPLATES["en"]

    def test_multi_vn_matches_dict(self):
        templates = get_multi_templates("vn")
        assert templates == MULTI_TEMPLATES["vn"]


class TestTemplatePlaceholders:
    def test_single_templates_have_ticker_placeholder(self):
        """Every single-template question should contain {ticker}."""
        for templates in SINGLE_TEMPLATES.values():
            for template in templates:
                question = template["question"]
                assert "{ticker}" in question, (
                    f"Single template '{template['title']}' missing {{ticker}} placeholder"
                )

    def test_multi_en_templates_have_ticker_reference(self):
        """English multi-template questions should reference tickers."""
        for template in MULTI_TEMPLATES["en"]:
            question = template["question"]
            has_ref = (
                "{ticker}" in question
                or "[TICKER]" in question
                or "ticker" in question.lower()
            )
            assert has_ref, (
                f"Multi template '{template['title']}' (en) has no ticker reference"
            )

    def test_multi_vn_templates_have_ticker_reference(self):
        """Vietnamese multi-template questions should reference tickers (mã)."""
        for template in MULTI_TEMPLATES["vn"]:
            question = template["question"]
            assert "mã" in question, (
                f"Multi template '{template['title']}' (vn) has no ticker reference"
            )
