#!/usr/bin/env python3
"""Central Job Aggregator CLI and Orchestrator.

Integrates with search_config.json, runs multi-query/multi-location searches across
Indeed and Xing, applies a strict 24-hour publication filter, and exports to:
  1) data/jobs_aggregated.json (full standard schema)
  2) data/jobs_aggregated.csv (full CSV)
  3) data/jobs.json (exact pt.txt format: title, company, jd, applysite)
  4) data/search_summary.json (search audit metadata)
"""

import argparse
import asyncio
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Setup local imports
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from indeed.indeed_scraper import collect_daily_jobs as scrape_indeed
from xing.xing_scraper import scrape_xing_cdp as scrape_xing
from stepstone.stepstone import scrape_stepstone_cdp as scrape_stepstone
from utils import is_within_24h, format_for_pt_output, generate_search_summary

DEFAULT_DATA_DIR = SCRIPT_DIR / "data"
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "search_config.json"

CSV_COLUMNS = [
    "job_id",
    "source",
    "job_title",
    "company_name",
    "location",
    "published",
    "job_url",
    "full_description",
    "scraped_at",
]


def export_to_csv(jobs: list, output_path: Path):
    """Exports standardized job list to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for j in jobs:
            writer.writerow(j)
    print(f"[+] Exported {len(jobs)} jobs to CSV: {output_path}")


def export_to_json(data, output_path: Path):
    """Exports data structure to JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[+] Exported to JSON: {output_path}")


def deduplicate_jobs(jobs: list) -> list:
    """Deduplicates jobs by job_url and (company, title, location)."""
    seen_urls = set()
    seen_signatures = set()
    unique_jobs = []

    for j in jobs:
        url = j.get("job_url", "").strip().lower().split("?")[0]
        comp = j.get("company_name", "").strip().lower()
        title = j.get("job_title", "").strip().lower()
        loc = j.get("location", "").strip().lower()
        sig = (comp, title, loc)

        if url and url in seen_urls:
            continue
        if comp != "n/a" and sig in seen_signatures:
            continue

        if url:
            seen_urls.add(url)
        if comp != "n/a":
            seen_signatures.add(sig)

        unique_jobs.append(j)

    return unique_jobs


def calculate_metrics(jobs: list) -> dict:
    """Calculates breakdown metrics for the final summary."""
    metrics = {
        "total_unique": len(jobs),
        "baden_wuerttemberg": 0,
        "bavaria": 0,
        "internships": 0,
        "working_student": 0,
        "software_engineering": 0,
        "data_ml_ai": 0,
        "computer_vision": 0,
        "llm_nlp_genai": 0,
    }

    bw_terms = ["baden-württemberg", "stuttgart", "karlsruhe", "mannheim", "heidelberg", "ulm", "freiburg", "aalen"]
    bayern_terms = ["bavaria", "bayern", "münchen", "munich", "nürnberg", "nuremberg", "augsburg", "erlangen"]

    for j in jobs:
        text = f"{j.get('job_title', '')} {j.get('location', '')} {j.get('full_description', '')}".lower()
        title = j.get("job_title", "").lower()

        if any(term in text for term in bw_terms):
            metrics["baden_wuerttemberg"] += 1
        if any(term in text for term in bayern_terms):
            metrics["bavaria"] += 1

        if any(term in title for term in ["intern", "praktik", "pflichtpraktik"]):
            metrics["internships"] += 1
        if any(term in title for term in ["werkstudent", "working student", "student"]):
            metrics["working_student"] += 1

        if any(term in text for term in ["software", "python", "backend", "developer", "engineer"]):
            metrics["software_engineering"] += 1
        if any(term in text for term in ["machine learning", "data science", "data engineer", "artificial intelligence", "ki", "ai"]):
            metrics["data_ml_ai"] += 1
        if any(term in text for term in ["computer vision", "bildverarbeitung", "yolo", "ocr", "image processing", "vision"]):
            metrics["computer_vision"] += 1
        if any(term in text for term in ["llm", "nlp", "genai", "generative ai", "rag", "langchain"]):
            metrics["llm_nlp_genai"] += 1

    return metrics


async def run_aggregator(
    search_pairs: list,
    max_pages: int = 1,
    sources: list = None,
    fetch_descriptions: bool = True,
    headless: bool = False,
    cdp_url: str = "http://localhost:9222",
    output_dir: Path = DEFAULT_DATA_DIR,
    export_format: str = "both",
    filter_24h: bool = True,
) -> list:
    if sources is None:
        sources = ["indeed", "xing"]

    timestamp = datetime.now(timezone.utc).isoformat()
    raw_collected_jobs = []

    print("=" * 65)
    print(f" JOB AGGREGATOR PIPELINE")
    print(f" Total Search Pairs: {len(search_pairs)}")
    print(f" Pages per Source:   {max_pages}")
    print(f" Sources:            {', '.join(sources).upper()}")
    print(f" 24-Hour Filter:     {'STRICT (Last 24h only)' if filter_24h else 'OFF'}")
    print("=" * 65)

    for idx, (keyword, location) in enumerate(search_pairs, 1):
        print(f"\n>>> [{idx}/{len(search_pairs)}] Query: '{keyword}' in '{location}'")

        # 1. Run Indeed
        if "indeed" in sources:
            try:
                indeed_jobs = await scrape_indeed(
                    keyword=keyword,
                    location=location,
                    max_pages=max_pages,
                    output_path=output_dir / "indeed_latest_batch.json",
                    fetch_descriptions=fetch_descriptions,
                    headless=headless,
                    filter_24h=filter_24h,
                )
                for j in indeed_jobs:
                    j["scraped_at"] = timestamp
                raw_collected_jobs.extend(indeed_jobs)
            except Exception as e:
                print(f"[✗] Indeed scraper error for '{keyword}': {e}")

        # 2. Run Xing
        if "xing" in sources:
            try:
                xing_jobs = await scrape_xing(
                    keyword=keyword,
                    location=location,
                    max_pages=max_pages,
                    cdp_url=cdp_url,
                    output_path=output_dir / "xing_latest_batch.json",
                    fetch_descriptions=fetch_descriptions,
                    filter_24h=filter_24h,
                )
                for j in xing_jobs:
                    j["scraped_at"] = timestamp
                raw_collected_jobs.extend(xing_jobs)
            except Exception as e:
                print(f"[✗] Xing scraper error for '{keyword}': {e}")

        # 3. Run StepStone
        if "stepstone" in sources:
            try:
                stepstone_jobs = await scrape_stepstone(
                    keyword=keyword,
                    location=location,
                    max_pages=max_pages,
                    cdp_url=cdp_url,
                    output_path=output_dir / "stepstone_latest_batch.json",
                    fetch_descriptions=fetch_descriptions,
                    filter_24h=filter_24h,
                )
                for j in stepstone_jobs:
                    j["scraped_at"] = timestamp
                raw_collected_jobs.extend(stepstone_jobs)
            except Exception as e:
                print(f"[✗] StepStone scraper error for '{keyword}': {e}")

    # 4. Post-filtering: Apply strict 24-hour verification
    if filter_24h:
        filtered_jobs = [
            j for j in raw_collected_jobs
            if is_within_24h(j.get("published", ""), source=j.get("source", ""))
        ]
        dropped_count = len(raw_collected_jobs) - len(filtered_jobs)
        if dropped_count > 0:
            print(f"[*] Filtered out {dropped_count} postings older than 24 hours.")
    else:
        filtered_jobs = raw_collected_jobs

    # 4. Deduplication
    unique_jobs = deduplicate_jobs(filtered_jobs)
    metrics = calculate_metrics(unique_jobs)

    print("\n" + "=" * 65)
    print(" AGGREGATION & METRICS REPORT")
    print("=" * 65)
    print(f"Total Unique 24h Jobs Found:     {metrics['total_unique']}")
    print(f" - Baden-Württemberg:            {metrics['baden_wuerttemberg']}")
    print(f" - Bavaria:                      {metrics['bavaria']}")
    print(f" - Internships / Praktika:       {metrics['internships']}")
    print(f" - Werkstudent / Working Student:{metrics['working_student']}")
    print(f" - Software Engineering:         {metrics['software_engineering']}")
    print(f" - Data / ML / AI:               {metrics['data_ml_ai']}")
    print(f" - Computer Vision:              {metrics['computer_vision']}")
    print(f" - LLM / NLP / GenAI:            {metrics['llm_nlp_genai']}")
    print("=" * 65)

    # 5. Export Files
    output_dir.mkdir(parents=True, exist_ok=True)
    final_jobs_dir = SCRIPT_DIR / "final_jobs"
    final_jobs_dir.mkdir(parents=True, exist_ok=True)

    # Format timestamp slug: hour_minute_date (e.g. 00_05_2026_09_13)
    now = datetime.now()
    timestamp_slug = now.strftime("%H_%M_%Y_%m_%d")

    # Files saved in final_jobs folder
    ts_json_path = final_jobs_dir / f"jobs_aggregated_{timestamp_slug}.json"
    ts_csv_path = final_jobs_dir / f"jobs_aggregated_{timestamp_slug}.csv"
    latest_json_path = final_jobs_dir / "jobs_aggregated.json"
    latest_csv_path = final_jobs_dir / "jobs_aggregated.csv"

    # Files saved in output_dir (data folder)
    ts_pt_jobs_path = output_dir / f"jobs_{timestamp_slug}.json"
    ts_summary_path = output_dir / f"search_summary_{timestamp_slug}.json"
    latest_pt_jobs_path = output_dir / "jobs.json"
    latest_summary_path = output_dir / "search_summary.json"

    # Export Standard JSON to final_jobs
    if export_format in ["json", "both"]:
        export_to_json(unique_jobs, ts_json_path)
        export_to_json(unique_jobs, latest_json_path)
        # Also maintain a copy in data folder
        export_to_json(unique_jobs, output_dir / f"jobs_aggregated_{timestamp_slug}.json")
        export_to_json(unique_jobs, output_dir / "jobs_aggregated.json")

    # Export Standard CSV to final_jobs
    if export_format in ["csv", "both"]:
        export_to_csv(unique_jobs, ts_csv_path)
        export_to_csv(unique_jobs, latest_csv_path)
        export_to_csv(unique_jobs, output_dir / f"jobs_aggregated_{timestamp_slug}.csv")
        export_to_csv(unique_jobs, output_dir / "jobs_aggregated.csv")

    # Export pt.txt exact schema (title, company, jd, applysite)
    pt_jobs = format_for_pt_output(unique_jobs)
    export_to_json(pt_jobs, ts_pt_jobs_path)
    export_to_json(pt_jobs, latest_pt_jobs_path)
    export_to_json(pt_jobs, final_jobs_dir / f"jobs_{timestamp_slug}.json")
    export_to_json(pt_jobs, final_jobs_dir / "jobs.json")

    # Export search audit summary
    summary_data = generate_search_summary(
        jobs=unique_jobs,
        locations_searched=list({p[1] for p in search_pairs}),
        categories_searched=list({p[0] for p in search_pairs}),
        sources_searched=sources,
        notes="Exhaustive search filtered strictly to last 24 hours.",
    )
    export_to_json(summary_data, ts_summary_path)
    export_to_json(summary_data, latest_summary_path)
    export_to_json(summary_data, final_jobs_dir / f"search_summary_{timestamp_slug}.json")
    export_to_json(summary_data, final_jobs_dir / "search_summary.json")

    print(f"\n[✓] Aggregated jobs successfully saved to '{final_jobs_dir}':")
    print(f"    - {ts_json_path.name}")
    print(f"    - {latest_json_path.name}")
    print(f"    - {ts_csv_path.name}")

    return unique_jobs


def load_search_pairs_from_config(
    config_path: Path,
    category_group: str = "high_priority",
    location_group: str = "regions",
    limit_queries: int = None,
) -> list:
    """Extracts (query, location) tuples from search_config.json."""
    if not config_path.exists():
        return [("Data Scientist", "Deutschland")]

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # 1. Resolve Queries
    query_dict = cfg.get("search_queries", {})
    if category_group == "high_priority":
        queries = query_dict.get("high_priority_queries", [])
    elif category_group == "computer_vision":
        queries = query_dict.get("computer_vision_queries", [])
    elif category_group == "llm_nlp_genai":
        queries = query_dict.get("llm_nlp_genai_queries", [])
    elif category_group == "software_and_data":
        queries = query_dict.get("software_and_data_queries", [])
    elif category_group == "all":
        queries = []
        for cat_list in query_dict.values():
            if isinstance(cat_list, list):
                queries.extend(cat_list)
    else:
        queries = query_dict.get("high_priority_queries", ["Data Scientist"])

    if limit_queries and limit_queries > 0:
        queries = queries[:limit_queries]

    # 2. Resolve Locations
    loc_dict = cfg.get("locations", {})
    if location_group == "regions":
        # Baden-Württemberg and Bavaria (as in pt.txt)
        locations = ["Baden-Württemberg", "Bayern"]
    elif location_group == "bw_cities":
        locations = loc_dict.get("baden_wuerttemberg_cities", ["Stuttgart"])
    elif location_group == "bavaria_cities":
        locations = loc_dict.get("bavaria_cities", ["München"])
    elif location_group == "remote":
        locations = ["Remote"]
    elif location_group == "all":
        locations = ["Baden-Württemberg", "Bayern"]
    else:
        locations = [location_group]

    pairs = []
    for q in queries:
        for loc in locations:
            pairs.append((q, loc))

    return pairs


def main():
    parser = argparse.ArgumentParser(
        description="Unified Job Aggregator CLI powered by search_config.json and 24-hour filtering."
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to search configuration JSON file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "-k", "--keyword", help="Direct search query override (ignores config queries if set)"
    )
    parser.add_argument(
        "-l", "--location", help="Direct location override (ignores config locations if set)"
    )
    parser.add_argument(
        "--category",
        choices=["high_priority", "computer_vision", "llm_nlp_genai", "software_and_data", "all"],
        default="high_priority",
        help="Query category from search_config.json to run (default: high_priority)",
    )
    parser.add_argument(
        "--locations-group",
        choices=["regions", "bw_cities", "bavaria_cities", "remote", "all"],
        default="regions",
        help="Location group to search (default: regions -> Baden-Württemberg & Bayern)",
    )
    parser.add_argument(
        "--limit-queries",
        type=int,
        default=5,
        help="Limit number of queries from config to execute per run (default: 5, pass 0 for all)",
    )
    parser.add_argument(
        "-p", "--max-pages", type=int, default=1, help="Number of pages to scrape per query/source (default: 1)"
    )
    parser.add_argument(
        "-s",
        "--sources",
        nargs="+",
        choices=["all", "indeed", "xing", "stepstone"],
        default=["all"],
        help="Sources to scrape (default: all)",
    )
    parser.add_argument(
        "--no-desc",
        dest="fetch_descriptions",
        action="store_false",
        help="Skip fetching full job descriptions for rapid collection",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Playwright browser in headless mode",
    )
    parser.add_argument(
        "--no-filter-24h",
        dest="filter_24h",
        action="store_false",
        help="Disable strict 24-hour publication date filtering",
    )
    parser.add_argument(
        "--cdp-url",
        default="http://localhost:9222",
        help="CDP URL for Chrome connection (Xing & StepStone, default: http://localhost:9222)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Output directory for JSON and CSV files",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=["json", "csv", "both"],
        default="both",
        help="Export format (default: both)",
    )

    args = parser.parse_args()

    # Determine search pairs
    if args.keyword and args.location:
        search_pairs = [(args.keyword, args.location)]
    elif args.keyword:
        search_pairs = [(args.keyword, "Baden-Württemberg"), (args.keyword, "Bayern")]
    else:
        limit = None if args.limit_queries == 0 else args.limit_queries
        search_pairs = load_search_pairs_from_config(
            config_path=args.config,
            category_group=args.category,
            location_group=args.locations_group,
            limit_queries=limit,
        )

    selected_sources = ["indeed", "xing", "stepstone"] if "all" in args.sources else args.sources

    asyncio.run(
        run_aggregator(
            search_pairs=search_pairs,
            max_pages=args.max_pages,
            sources=selected_sources,
            fetch_descriptions=args.fetch_descriptions,
            headless=args.headless,
            cdp_url=args.cdp_url,
            output_dir=args.output_dir,
            export_format=args.format,
            filter_24h=args.filter_24h,
        )
    )


if __name__ == "__main__":
    main()
