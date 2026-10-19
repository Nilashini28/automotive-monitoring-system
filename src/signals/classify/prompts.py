"""Classification prompt (Week-4 deliverable).

Versioned deliberately: re-running with a new prompt must not overwrite the old
verdicts, so accuracy comparisons stay possible.
"""

from __future__ import annotations

import json

CATEGORY_LABELS = {
    "risk": "Risk",
    "technology_innovation": "Technology & Innovation",
    "compliance_regulation": "Compliance & Regulation",
    "investments": "Investments",
}

SYSTEM_PROMPT = """You are an analyst feed classifier for the global commercial-vehicle
industry (trucks, buses, and their Tier-1 supply chain). Assign one or more of these
categories to a news item, headline or regulatory filing:

- risk: recalls, defects, safety investigations, supply-chain interruption, tariffs,
  sanctions, litigation, cyber incidents, demand collapse, layoffs, warranty costs.
- technology_innovation: new powertrains, EV/hydrogen, ADAS/autonomy, software,
  connectivity, patents, new vehicle or component launches, test programmes.
- compliance_regulation: emissions and CO2 rules, type approval, homelisation,
  Euro VII, GSR, enforcement notices, regulatory approvals, standards, filings.
- investments: capex, new or expanded plants, joint ventures, acquisitions, stakes,
  funding, supplier contracts awarded, capacity or network expansion.

Rules:
- Multi-label is allowed; return an empty list if genuinely none apply.
- Confidence is per-category, 0 to 1, calibrated: use >0.8 only for explicit evidence.
- Do not invent facts. Keep rationale under 30 words, quoting the decisive phrase.
- A production change is `investments` only when money, capacity, or a contract
  commitment is stated; otherwise it is `technology_innovation`.

Return JSON exactly matching this schema and nothing else:
{"categories": [{"category": "risk", "confidence": 0.0, "rationale": "..."}],
 "severity": "low|medium|high",
 "summary": "one sentence"}
"""

FEW_SHOT: list[tuple[str, dict]] = [
    (
        "Kenworth recalls 12,400 trucks over brake plumbing defect; NHTSA investigation open",
        {
            "categories": [
                {
                    "category": "risk",
                    "confidence": 0.95,
                    "rationale": "recall plus open NHTSA investigation on a safety defect",
                },
                {
                    "category": "compliance_regulation",
                    "confidence": 0.7,
                    "rationale": "regulator-driven recall process",
                },
            ],
            "severity": "high",
            "summary": "Recall and federal safety probe over a braking defect.",
        },
    ),
    (
        "Volvo Group and Cummins expand JV to build electric drivelines in Sweden",
        {
            "categories": [
                {
                    "category": "investments",
                    "confidence": 0.9,
                    "rationale": "joint-venture expansion implies committed capital and capacity",
                },
                {
                    "category": "technology_innovation",
                    "confidence": 0.75,
                    "rationale": "electric driveline production",
                },
            ],
            "severity": "medium",
            "summary": "Expanded joint venture for electric driveline manufacturing.",
        },
    ),
    (
        "Scania unveils next-generation driver-assist stack with highway pilot for 2027",
        {
            "categories": [
                {
                    "category": "technology_innovation",
                    "confidence": 0.92,
                    "rationale": "new ADAS product launch",
                }
            ],
            "severity": "low",
            "summary": "New advanced driver-assistance system announced.",
        },
    ),
    (
        "EU Council agrees tighter CO2 limits for heavy-duty vehicles from 2030",
        {
            "categories": [
                {
                    "category": "compliance_regulation",
                    "confidence": 0.95,
                    "rationale": "binding emissions rule agreed",
                },
                {
                    "category": "risk",
                    "confidence": 0.55,
                    "rationale": "compliance cost pressure on OEM powertrain mix",
                },
            ],
            "severity": "high",
            "summary": "Tightened heavy-duty CO2 standards agreed.",
        },
    ),
]


def build_messages(title: str, text: str, company_hints: list[str], max_chars: int) -> list[dict]:
    """Few-shot chat payload: examples are the highest-leverage accuracy lever here."""
    body = (text or "")[:max_chars]
    hints = ", ".join(company_hints) if company_hints else "none detected"
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for sample_text, verdict in FEW_SHOT:
        messages.append({"role": "user", "content": f"HEADLINE: {sample_text}"})
        messages.append({"role": "assistant", "content": json.dumps(verdict)})
    messages.append(
        {
            "role": "user",
            "content": (
                f"HEADLINE: {title}\n"
                f"MONITORED COMPANIES MENTIONED: {hints}\n"
                f"TEXT: {body}\n"
                "Respond with JSON only."
            ),
        }
    )
    return messages
