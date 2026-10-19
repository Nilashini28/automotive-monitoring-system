"""Streamlit dashboard (Week-6 deliverable).

Reads exclusively through the FastAPI service - never the database - so the API
stays the single source of truth and the two can run in separate containers.

    streamlit run src/signals/web/app.py
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st

API_URL = os.getenv("SIGNALS_API_URL", "http://127.0.0.1:8000").rstrip("/")
API_KEY = os.getenv("API_TOKEN", "")
CATEGORY_COLORS = {
    "risk": "#e4572e",
    "technology_innovation": "#2e86ab",
    "compliance_regulation": "#b8af3f",
    "investments": "#7d6bb5",
}
CATEGORY_LABEL = {
    "risk": "Risk",
    "technology_innovation": "Technology & Innovation",
    "compliance_regulation": "Compliance & Regulation",
    "investments": "Investments",
}

st.set_page_config(page_title="Automotive Signals", page_icon="", layout="wide")


@st.cache_data(ttl=60, show_spinner=False)
def api(path: str, params: tuple | None = None) -> object:
    """params arrives as a tuple so st.cache_data can hash it."""
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    response = httpx.get(
        f"{API_URL}{path}", params=dict(params or ()), headers=headers, timeout=30
    )
    response.raise_for_status()
    return response.json()


def _clean(params: dict) -> tuple:
    return tuple((k, v) for k, v in params.items() if v not in (None, "", [], False))


def _stop_with_help(exc: Exception) -> None:
    st.error(
        f"Cannot reach the API at **{API_URL}** ({type(exc).__name__}: {exc}). Start it with "
        "`uvicorn signals.api.app:app --port 8000`, or point `SIGNALS_API_URL` at the deployed host."
    )
    st.stop()


try:
    meta = api("/filters")
    stats = api("/stats")
    health = api("/healthz")
except Exception as exc:  # noqa: BLE001
    _stop_with_help(exc)

companies = meta["companies"]
oem_names = [c["short_name"] for c in companies if c["type"] == "oem"]
vendor_names = [c["short_name"] for c in companies if c["type"] == "tier1"]

# ------------------------------------------------------------------ sidebar --
with st.sidebar:
    st.title("Automotive Signals")
    st.caption("Commercial-vehicle OEMs + Tier-1 supply chain monitoring")

    picked = st.multiselect("OEMs", oem_names, default=oem_names) + st.multiselect(
        "Tier-1 vendors", vendor_names
    )
    company_ids = [c["id"] for c in companies if c["short_name"] in set(picked)] or None
    categories = st.multiselect("Categories", list(CATEGORY_LABEL.values()), default=list(CATEGORY_LABEL.values()))
    category_values = [k for k, v in CATEGORY_LABEL.items() if v in categories] or None

    today = datetime.now(timezone.utc).date()
    window = st.date_input(
        "Lookback window (UTC)",
        value=(today - timedelta(days=14), today + timedelta(days=1)),
        min_value=date(2020, 1, 1),
        max_value=today + timedelta(days=1),
    )
    severity = st.multiselect("Severity", ["high", "medium", "low"])
    min_conf = st.slider("Minimum confidence", 0.0, 1.0, 0.0, 0.05)
    highlight_only = st.toggle("High-signal only", value=False)
    page_size = st.select_slider("Items per page", [10, 25, 50, 100], value=25)
    query = st.text_input("Search title and body", placeholder="recall, hydrogen, tariffs")
    st.divider()
    st.caption(
        f"{stats['corpus']['articles']} articles · {stats['corpus']['sources']} sources · "
        f"classifier `{health['provider']}`"
    )

start, end = (window[0], window[1]) if isinstance(window, tuple) and len(window) == 2 else (today, today)
params = {
    "company": company_ids,
    "category": category_values,
    "severity": severity,
    "date_from": f"{start.isoformat()}T00:00:00Z",
    "date_to": f"{end.isoformat()}T23:59:59Z",
    "min_confidence": min_conf or None,
    "highlight_only": highlight_only or None,
}
endpoint = "/search" if len(query.strip()) >= 2 else "/events"
if endpoint == "/search":
    params["q"] = query.strip()

page = 1 if endpoint == "/search" else st.number_input("Page", min_value=1, value=1, step=1)

try:
    feed = api(
        endpoint,
        _clean({**params, "limit": page_size, "offset": (int(page) - 1) * page_size}),
    )
    trends = api("/trends", _clean({"days": max(1, (end - start).days), "category": (category_values or [None])[0]}))
except Exception as exc:  # noqa: BLE001
    _stop_with_help(exc)

items, total = feed["items"], feed["total"]

# ------------------------------------------------------------------- metrics --
c1, c2, c3, c4 = st.columns(4)
c1.metric("Matching events", f"{total:,}")
c2.metric("High severity on page", sum(1 for i in items if i["severity"] == "high"))
c3.metric("Highlighted on page", sum(1 for i in items if i["highlight"]))
top = (stats["top_companies"] or [{"company": "-", "events": 0}])[0]
c4.metric("Busiest company", top["company"], f"{top['events']} events / 30d")

# -------------------------------------------------------------------- charts --
left, right = st.columns([2, 1])
with left:
    st.subheader("Signal trend")
    if trends:
        frame = pd.DataFrame(trends)
        frame["date"] = pd.to_datetime(frame["date"])
        chart = px.line(frame, x="date", y="count", color="category",
                        color_discrete_map=CATEGORY_COLORS, markers=True)
        chart.update_layout(height=300, legend_title="", yaxis_title="Labelled events", xaxis_title="")
        st.plotly_chart(chart, use_container_width=True)
    else:
        st.info("No dated events in this window yet - run the ingest worker.")
with right:
    st.subheader("Category mix")
    if stats["by_category"]:
        bar = px.bar(pd.DataFrame(stats["by_category"]), x="count", y="label", orientation="h",
                     color="category", color_discrete_map=CATEGORY_COLORS)
        bar.update_layout(height=300, showlegend=False, yaxis_title="", xaxis_title="Labels (30d)")
        st.plotly_chart(bar, use_container_width=True)

st.divider()
st.subheader("Event feed")
if not items:
    st.warning("No events match these filters. Widen the lookback window or clear a category.")

for entry in items:
    published = datetime.fromisoformat(entry["published_at"].replace("Z", "+00:00"))
    row = st.container(border=True)
    title = f"**{entry['title']}**" if entry["highlight"] else entry["title"]
    row.markdown(title)
    row.caption(
        f"{published:%Y-%m-%d %H:%M} UTC · {entry['source']} ({entry['source_kind']}) · "
        f"severity {entry['severity'] or '-'} · confidence {entry['confidence']:.2f} · {entry['model']}"
    )
    if entry["categories"]:
        row.markdown(
            " ".join(
                f"`{CATEGORY_LABEL.get(c['category'], c['category'])} {c['confidence']:.2f}`"
                for c in entry["categories"]
            )
        )
    if entry["summary"]:
        text = entry["summary"]
        row.markdown(text[:350] + ("…" if len(text) > 350 else ""))
    with row.expander("Evidence and links"):
        if entry["categories"]:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "category": CATEGORY_LABEL.get(c["category"], c["category"]),
                            "confidence": c["confidence"],
                            "rationale": c["rationale"],
                        }
                        for c in entry["categories"]
                    ]
                ),
                hide_index=True,
                use_container_width=True,
            )
        st.markdown(
            f"**Mentions:** {', '.join(entry['companies']) or 'none detected'} · "
            f"**Words:** {entry['word_count']}"
        )
        st.page_link(entry["url"], label="Open source article")

st.caption(f"Page {page} · showing {len(items)} of {total} · API {API_URL}")
