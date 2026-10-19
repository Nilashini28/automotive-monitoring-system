"""Evaluation harness maths - including the trap that inflated early scores."""

from __future__ import annotations

from signals.db.models import ValidationLabel
from signals.eval.harness import export_template, score


def test_perfect_classifier_scores_one(session, make_article):
    a = make_article(title="Recall", categories=["risk"], confidence=0.9)
    b = make_article(title="Plant", categories=["investments"], confidence=0.8)
    session.add_all(
        [ValidationLabel(article_id=a.id, categories=["risk"]),
         ValidationLabel(article_id=b.id, categories=["investments"])]
    )
    session.flush()
    result = score(session)
    assert result["samples"] == 2
    assert result["exact_set_accuracy"] == 1.0
    assert result["macro_f1"] == 1.0


def test_gold_row_with_no_categories_still_counts_false_positives(session, make_article):
    """Regression: rows labelled `[]` used to be dropped, hiding false alarms."""
    article = make_article(title="Quarterly results", categories=["risk"], confidence=0.6)
    session.add(ValidationLabel(article_id=article.id, categories=[]))
    session.flush()
    result = score(session)
    assert result["samples"] == 1
    assert result["exact_set_accuracy"] == 0.0
    assert result["per_category"]["risk"]["precision"] == 0.0
    assert result["micro"]["precision"] == 0.0


def test_confidence_threshold_can_reach_perfect_precision(session, make_article):
    weak = make_article(title="Noise", categories=["risk"], confidence=0.3)
    strong = make_article(title="Real recall", categories=["risk"], confidence=0.95)
    session.add_all(
        [ValidationLabel(article_id=weak.id, categories=[]),
         ValidationLabel(article_id=strong.id, categories=["risk"])]
    )
    session.flush()
    assert score(session, min_confidence=0.0)["per_category"]["risk"]["precision"] == 0.5
    tuned = score(session, min_confidence=0.5)
    assert tuned["per_category"]["risk"]["precision"] == 1.0
    assert tuned["per_category"]["risk"]["recall"] == 1.0


def test_multi_label_partial_match_is_rewarded_by_overlap_not_exact(session, make_article):
    article = make_article(title="Recall and rule change", categories=["risk"], confidence=0.9)
    session.add(ValidationLabel(article_id=article.id, categories=["risk", "compliance_regulation"]))
    session.flush()
    result = score(session)
    assert result["exact_set_accuracy"] == 0.0
    assert result["any_overlap_accuracy"] == 1.0
    assert result["per_category"]["risk"]["recall"] == 1.0
    assert result["per_category"]["compliance_regulation"]["recall"] == 0.0


def test_export_template_lists_each_article_once(session, watchlist, make_article, tmp_path):
    both = make_article(title="Both buckets", categories=["risk", "investments"], confidence=0.9)
    out = export_template(session, n=8, path=tmp_path / "t.json")
    ids = [row["article_id"] for row in __import__("json").loads(out.read_text(encoding="utf-8"))]
    assert both.id in ids
    assert len(ids) == len(set(ids))
