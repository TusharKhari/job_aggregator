import asyncio
import json
import random
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus
from playwright.async_api import async_playwright

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils import is_within_24h

DEFAULT_OUTPUT_JSON = PROJECT_ROOT / "agent_jobs_payload.json"
DEFAULT_SEEN_JOBS_DB = PROJECT_ROOT / "seen_job_keys.txt"


def load_seen_keys(filepath: Path = DEFAULT_SEEN_JOBS_DB) -> set:
    if not filepath.exists():
        return set()
    with open(filepath, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def record_seen_keys(keys: set, filepath: Path = DEFAULT_SEEN_JOBS_DB):
    if not keys:
        return
    with open(filepath, "a", encoding="utf-8") as f:
        for k in sorted(keys):
            f.write(f"{k}\n")


async def get_job_description(context, job_url: str) -> str:
    """Visits the individual job page to extract the full job description."""
    page = await context.new_page()
    description = ""
    try:
        await page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
        desc_elem = page.locator(
            "#jobDescriptionText, div.jobsearch-JobComponent-description, div#jobDescriptionSection"
        ).first
        if await desc_elem.is_visible(timeout=5000):
            description = (await desc_elem.inner_text()).strip()
        else:
            description = "Description not visible or requires login."
    except Exception as e:
        description = f"Description could not be retrieved ({e})."
    finally:
        try:
            await page.close()
        except Exception:
            pass
    return description


async def extract_date_from_card(card) -> str:
    """Extracts posting date from job card using multiple selector and text fallbacks."""
    # Selector-based attempts
    selectors = [
        "span[data-testid='myJobsStateDate']",
        "span.date",
        "span[class*='date']",
        "div.jobMetaDataGroup span",
        "div.underShelfFooter span",
    ]
    for sel in selectors:
        elem = card.locator(sel).first
        if await elem.count() > 0:
            text = (await elem.inner_text()).strip()
            if text and any(term in text.lower() for term in ["vor", "tag", "stunde", "neu", "posted", "active", "aktiv"]):
                return text

    # Regex fallback on all card text lines
    try:
        card_text = await card.inner_text()
        for line in card_text.split("\n"):
            line = line.strip()
            if re.search(r"(vor\s+\d+\s+(tag|stunde|woche)|geschaltet|aktiv\s+vor|posted\s+\d+|just\s+posted|heute)", line, re.IGNORECASE):
                return line
    except Exception:
        pass

    return "N/A"


async def collect_daily_jobs(
    keyword: str = "Data Scientist",
    location: str = "Deutschland",
    max_pages: int = 2,
    output_path: Path = DEFAULT_OUTPUT_JSON,
    fetch_descriptions: bool = True,
    headless: bool = False,
    filter_24h: bool = True,
) -> list:
    seen_keys = load_seen_keys()
    new_jobs_for_agent = []
    newly_seen_keys = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"]
        )

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="de-DE"
        )
        page = await context.new_page()

        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        encoded_kw = quote_plus(keyword)
        encoded_loc = quote_plus(location)

        # 1. Collect job cards from pages
        cards_metadata = []
        for page_idx in range(max_pages):
            start_offset = page_idx * 10
            url = f"https://de.indeed.com/jobs?q={encoded_kw}&l={encoded_loc}&sort=date&fromage=1&start={start_offset}"

            print(f"\n--- Scraping Indeed Page {page_idx + 1}/{max_pages} ---")
            print(f"Navigating to {url}...")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print(f"[!] Warning: Navigation error: {e}")

            # Dismiss cookies if shown
            try:
                cookie_btn = page.locator("#onetrust-accept-btn-handler, button:has-text('Alle akzeptieren')")
                if await cookie_btn.is_visible(timeout=2500):
                    await cookie_btn.click()
            except Exception:
                pass

            await page.wait_for_timeout(2000)

            cards = await page.locator("div.job_seen_beacon, li:has(div.job_seen_beacon)").all()
            print(f"Found {len(cards)} card elements on page {page_idx + 1}...")

            for card in cards:
                link_elem = card.locator("a[data-jk], a.jcs-JobTitle, h2 a").first
                if await link_elem.count() == 0:
                    continue

                # Resilient data-jk extraction
                data_jk = await link_elem.get_attribute("data-jk")
                if not data_jk:
                    data_jk = await card.get_attribute("data-jk")
                if not data_jk:
                    href = await link_elem.get_attribute("href") or ""
                    match = re.search(r"jk=([a-zA-Z0-9]+)", href)
                    if match:
                        data_jk = match.group(1)

                if not data_jk or data_jk in seen_keys or data_jk in newly_seen_keys:
                    continue

                title = (await link_elem.inner_text()).strip()
                company_elem = card.locator("span[data-testid='company-name']").first
                company = (await company_elem.inner_text()).strip() if await company_elem.count() > 0 else "N/A"

                raw_date = await extract_date_from_card(card)
                if filter_24h and not is_within_24h(raw_date, source="indeed"):
                    continue

                clean_url = f"https://de.indeed.com/viewjob?jk={data_jk}"
                newly_seen_keys.add(data_jk)

                cards_metadata.append({
                    "id": data_jk,
                    "title": title,
                    "company": company,
                    "published": raw_date,
                    "url": clean_url
                })

            await asyncio.sleep(random.uniform(2.0, 3.5))

        # 2. Extract full job descriptions if enabled
        print(f"\nDiscovered {len(cards_metadata)} new listings. Fetching job descriptions...")
        for idx, item in enumerate(cards_metadata, 1):
            desc = ""
            if fetch_descriptions:
                print(f"[{idx}/{len(cards_metadata)}] Fetching description for '{item['title']}'...")
                desc = await get_job_description(context, item["url"])
                await asyncio.sleep(random.uniform(1.0, 2.0))

            new_jobs_for_agent.append({
                "job_id": item["id"],
                "source": "indeed",
                "job_title": item["title"],
                "company_name": item["company"],
                "location": location,
                "published": item["published"],
                "job_url": item["url"],
                "full_description": desc
            })

        await browser.close()

    # Save output JSON
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(new_jobs_for_agent, f, ensure_ascii=False, indent=2)

    record_seen_keys(newly_seen_keys)
    print(f"\nPayload ready: {len(new_jobs_for_agent)} jobs written to '{output_path}'.")
    return new_jobs_for_agent


if __name__ == "__main__":
    asyncio.run(collect_daily_jobs())