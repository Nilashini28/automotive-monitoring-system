"""Accuracy evaluation for the classifier (Week-4 deliverable).

Flow:
  python -m signals.eval.harness export --n 25   -> data/validation_template.json
  (a human fills in `gold` for each row)
  python -m signals.eval.harness score --labels data/gold_labels.json

Metrics are per-category precision / recall / F1 over a multi-label setting,
plus exact-set accuracy and micro/macro F1.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from signals.classify.prompts import CATEGORY_LABELS
from signals.config import PROJECT_ROOT
from signals.db.models import Article, CATEGORIES, Classification, ValidationLabel
from signals.db.session import create_all, session_scope

DEFAULT_TEMPLATE = PROJECT_ROOT / "data" / "validation_template.json"


def _best_per_article(session: Session, model: str | None = None) -> dict[int, Classification]:
    stmt = select(Classification).order_by(
        Classification.article_id, Classification.confidence.desc(), Classification.id.desc()
    )
    if model:
        stmt = stmt.where(Classification.model == model)
    rows = session.scalars(stmt)
    out: dict[int, Classification] = {}
    for row in rows:
        out.setdefault(row.article_id, row)
    return out


def export_template(session: Session, n: int = 25, path: Path = DEFAULT_TEMPLATE) -> Path:
    """Stratified sample: pull from each category so the set is not all `risk`."""
    best = _best_per_article(session)
    per_bucket = max(1, n // len(CATEGORIES))
    chosen: list[dict] = []
    used: set[int] = set()

    def record(article_id: int) -> None:
        if article_id in used:
            return
        article = session.get(Article, article_id)
        if article is None:
            return
        used.add(article_id)
        chosen.append(
            {
                "article_id": article.id,
                "title": article.title,
                "predicted": [e.get("category") for e in (best[article_id].categories or [])],
                "predicted_confidence": round(best[article_id].confidence, 2),
                "gold": [],
                "gold_note": "fill with any of: " + ", ".join(CATEGORIES),
            }
        )

    for category in CATEGORIES:
        ids = [
            aid
            for aid, cls in best.items()
            if any(entry.get("category") == category for entry in (cls.categories or []))
        ]
        for article_id in sorted(ids)[:per_bucket]:
            record(article_id)

    for article_id in sorted(aid for aid in best if aid not in used)[: max(0, n - len(chosen))]:
        record(article_id)
    path.write_text(json.dumps(chosen, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_labels(session: Session, path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    stored = 0
    for entry in data:
        raw = entry.get("gold")
        if raw is None:
            # An unfilled template row. An explicitly empty list is a real label
            # meaning "no category applies" and must be kept, or false positives
            # disappear and accuracy is inflated.
            continue
        gold = [c for c in raw if c in CATEGORIES]
        if session.get(Article, entry["article_id"]) is None:
            continue
        row = session.scalar(select(ValidationLabel).where(ValidationLabel.article_id == entry["article_id"]))
        if row is None:
            row = ValidationLabel(article_id=entry["article_id"])
            session.add(row)
        row.categories = gold
        row.labeler = entry.get("labeler", "manual")
        stored += 1
    session.flush()
    return stored


def score(session: Session, min_confidence: float = 0.0, model: str | None = None) -> dict:
    gold_rows = list(session.scalars(select(ValidationLabel)))
    best = _best_per_article(session, model)
    if not gold_rows:
        return {"error": "no gold labels stored - run `load` first"}

    tp = {c: 0 for c in CATEGORIES}
    fp = {c: 0 for c in CATEGORIES}
    fn = {c: 0 for c in CATEGORIES}
    exact = 0
    overlap = 0
    pairs: list[tuple[set[str], set[str]]] = []

    for gold_row in gold_rows:
        cls = best.get(gold_row.article_id)
        if cls is None:
            continue
        predicted = {
            e.get("category")
            for e in (cls.categories or [])
            if float(e.get("confidence") or 0.0) >= min_confidence
        }
        gold_set = set(gold_row.categories)
        pairs.append((gold_set, predicted))
        if predicted == gold_set:
            exact += 1
        if predicted & gold_set:
            overlap += 1
        for category in CATEGORIES:
            if category in gold_set and category in predicted:
                tp[category] += 1
            elif category in predicted:
                fp[category] += 1
            elif category in gold_set:
                fn[category] += 1

    sample_count = len(pairs)

    per_category = {}
    macro: list[float] = []
    for category in CATEGORIES:
        support = tp[category] + fn[category]
        p = tp[category] / (tp[category] + fp[category]) if tp[category] + fp[category] else 0.0
        r = tp[category] / support if support else 0.0
        f1 = (2 * p * r / (p + r)) if p + r else 0.0
        per_category[category] = {
            "label": CATEGORY_LABELS[category],
            "support": support,
            "predicted": tp[category] + fp[category],
            "precision": round(p, 3),
            "recall": round(r, 3),
            "f1": round(f1, 3),
        }
        # A category absent from gold and never predicted carries no information;
        # scoring it 0.0 would drag macro F1 down to a floor of 1/len(CATEGORIES).
        if support or tp[category] + fp[category]:
            macro.append(f1)

    total_tp, total_fp, total_fn = sum(tp.values()), sum(fp.values()), sum(fn.values())
    micro_p = total_tp / (total_tp + total_fp) if total_tp + total_fp else 0.0
    micro_r = total_tp / (total_tp + total_fn) if total_tp + total_fn else 0.0
    return {
        "samples": sample_count,
        "model": model or "best per article",
        "min_confidence": min_confidence,
        "exact_set_accuracy": round(exact / sample_count, 3) if sample_count else 0.0,
        "any_overlap_accuracy": round(overlap / sample_count, 3) if sample_count else 0.0,
        "micro": {"precision": round(micro_p, 3), "recall": round(micro_r, 3)},
        "macro_f1": round(sum(macro) / len(macro), 3) if macro else 0.0,
        "macro_over_categories": len(macro),
        "per_category": per_category,
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="signals.eval.harness")
    sub = parser.add_subparsers(dest="command", required=True)
    p_export = sub.add_parser("export")
    p_export.add_argument("--n", type=int, default=25)
    p_export.add_argument("--out", type=Path, default=DEFAULT_TEMPLATE)
    p_load = sub.add_parser("load")
    p_load.add_argument("--labels", type=Path, required=True)
    p_score = sub.add_parser("score")
    p_score.add_argument("--min-confidence", type=float, default=0.0)
    p_score.add_argument("--model", default=None, help="restrict to one classifier version")
    p_score.add_argument("--labels", type=Path, default=None)
    args = parser.parse_args()

    create_all()
    with session_scope() as session:
        if args.command == "export":
            path = export_template(session, args.n, args.out)
            print(f"template written: {path}")
        elif args.command == "load":
            print(f"gold labels stored: {load_labels(session, args.labels)}")
        else:
            if args.labels:
                print(f"gold labels stored: {load_labels(session, args.labels)}")
            print(json.dumps(score(session, args.min_confidence, args.model), indent=2))


if __name__ == "__main__":
    main()
