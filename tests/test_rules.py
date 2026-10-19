"""Rule classifier: the offline baseline the LLM must beat."""

from __future__ import annotations

from signals.classify.rules import classify


def _categories(title, body="", kind="rss"):
    return [c.category for c in classify(title, body, kind).categories]


def test_recall_is_risk_and_compliance():
    verdict = classify("Paccar recalls 6,000 trucks over brake defect, NHTSA investigation open", "")
    assert "risk" in _categories("Paccar recalls 6,000 trucks over brake defect, NHTSA investigation open")
    assert verdict.primary == "risk"
    assert verdict.severity == "high"


def test_investment_language_lands_on_investments():
    cats = _categories(
        "Daimler Truck to invest $500 million in new electric truck plant, creating 1,000 jobs"
    )
    assert "investments" in cats


def test_plain_quarterly_results_get_no_category():
    assert _categories("Traton reports second-quarter sales rose 4%", "") == []


def test_regulator_source_boosts_compliance():
    assert "compliance_regulation" in _categories("Annual report filed", "10-K", kind="regulator")


def test_confidence_is_capped_and_sorted():
    verdict = classify(
        "Recall investigation: defect, lawsuit, tariff and shortage hit the supplier simultaneously",
        "emissions regulation and new plant investment too",
    )
    scores = [c.confidence for c in verdict.categories]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= s <= 0.9 for s in scores)
