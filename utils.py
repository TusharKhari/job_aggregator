"""Shared utilities for job filtering, date verification, and data formatting."""

import re
from datetime import datetime, timezone


def is_within_24h(date_str: str, source: str = "") -> bool:
    """Evaluates whether a job posting date string indicates the job was posted within the last 24 hours.

    Handles German and English relative date strings:
    - 'vor 2 Stunden', 'vor 30 Minuten', 'gerade eben', 'heute', 'today', 'just posted' -> True
    - 'vor 1 Tag', '1 Tag', '1 day ago', '1d' -> True
    - 'vor 2 Tagen', 'vor 3 Tagen', 'vor 1 Woche', '30+ Tage', 'vor einem Monat' -> False
    - 'N/A': If source is Indeed (which uses server-side fromage=1), True; otherwise False if strict.
    """
    if not date_str or date_str.strip().upper() == "N/A":
        # Indeed applies fromage=1 on search query, so N/A date on card is assumed within 24h
        return source.lower() == "indeed"

    t = date_str.lower().strip()

    # Reject explicitly older postings (2 or more days, weeks, months)
    if re.search(r"vor\s+([2-9]|\d{2,})\s+tag", t):
        return False
    if re.search(r"\b([2-9]|\d{2,})\s+tag", t):
        return False
    if re.search(r"\b([2-9]|\d{2,})\s+day", t):
        return False
    if any(kw in t for kw in ["woche", "week", "monat", "month", "jahr", "year", "30+"]):
        return False

    # Accept explicitly recent postings (< 24 hours)
    if any(kw in t for kw in [
        "stunde", "stunden", "hour", "hours", "minute", "minuten", "min", "sekunde", "second",
        "heute", "today", "gerade", "just posted", "posted today", "neu", "new", "aktiv vor wenigen"
    ]):
        return True

    # 1 day / vor 1 Tag counts as last 24h
    if re.search(r"vor\s+1\s+tag", t) or re.search(r"\b1\s+tag", t) or re.search(r"\b1\s+day", t) or re.search(r"\b1d\b", t):
        return True

    # Fallback: if from Indeed with fromage=1, allow unless marked old
    return source.lower() == "indeed"


def format_for_pt_output(jobs: list) -> list:
    """Formats standardized job dictionaries to the exact pt.txt schema:
    [
        {
            "title": ...,
            "company": ...,
            "jd": ...,
            "applysite": ...
        }
    ]
    """
    formatted = []
    for j in jobs:
        formatted.append({
            "title": j.get("job_title", ""),
            "company": j.get("company_name", ""),
            "jd": j.get("full_description") or f"Position: {j.get('job_title', '')} at {j.get('company_name', '')}. Location: {j.get('location', '')}. Published: {j.get('published', '')}. URL: {j.get('job_url', '')}",
            "applysite": j.get("job_url", ""),
        })
    return formatted


def generate_search_summary(
    jobs: list,
    locations_searched: list,
    categories_searched: list,
    sources_searched: list,
    notes: str = "Search executed with 24-hour filter.",
) -> dict:
    """Generates the search_summary.json metadata object specified in pt.txt."""
    return {
        "search_timestamp": datetime.now(timezone.utc).isoformat(),
        "filter": "last 24 hours",
        "locations": locations_searched,
        "total_unique_jobs": len(jobs),
        "categories_searched": categories_searched,
        "sources_searched": sources_searched,
        "notes": notes,
    }
