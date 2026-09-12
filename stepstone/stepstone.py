import asyncio
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus, urljoin
# pyrefly: ignore [missing-import]
from playwright.async_api import async_playwright

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils import is_within_24h

DEFAULT_OUTPUT_JSON = SCRIPT_DIR / "stepstone_agent_jobs.json"
SEEN_KEYS_FILE = SCRIPT_DIR / "seen_stepstone_keys.txt"
MAX_PAGES = 2


def load_seen_keys(filepath: Path = SEEN_KEYS_FILE) -> set:
    if not filepath.exists():
        return set()
    with open(filepath, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def record_seen_keys(keys: set, filepath: Path = SEEN_KEYS_FILE):
    if not keys:
        return
    with open(filepath, "a", encoding="utf-8") as f:
        for k in sorted(keys):
            f.write(f"{k}\n")


def extract_stepstone_job_id(url: str) -> str | None:
    """Extracts numeric job ID from a StepStone job URL (e.g. ...--1234567-inline.html)."""
    match = re.search(r"--(\d+)(?:-inline)?(?:\.html)?", url)
    return match.group(1) if match else None


async def extract_date_from_article(article) -> str:
    """Extracts posting date from a StepStone job card using selectors and text fallbacks."""
    selectors = [
        "time",
        "[data-genesis-element='METADATA_ITEM_DATE']",
        "span[class*='Date']",
        "span[class*='Time']",
        "span[class*='date']",
        "span[class*='time']",
    ]
    for sel in selectors:
        elem = article.locator(sel).first
        if await elem.count() > 0:
            text = (await elem.inner_text()).strip()
            if text and any(term in text.lower() for term in ["vor", "tag", "stunde", "minute", "neu", "heute", "gestern"]):
                return text

    # Fallback line scanning
    try:
        raw_text = await article.inner_text()
        for line in raw_text.split("\n"):
            line = line.strip()
            if re.search(r"(vor\s+\d+\s+(tag|stunde|minute)|heute|gestern|neu|just\s+posted)", line, re.IGNORECASE):
                return line
    except Exception:
        pass

    return "N/A"


async def scrape_stepstone_cdp(
    keyword: str = "Data Scientist",
    location: str = "Deutschland",
    max_pages: int = MAX_PAGES,
    cdp_url: str = "http://localhost:9222",
    output_path: Path = DEFAULT_OUTPUT_JSON,
    fetch_descriptions: bool = False,
    filter_24h: bool = True,
) -> list:
    seen_keys = load_seen_keys()
    newly_seen_keys = set()
    jobs = []

    async with async_playwright() as p:
        print(f"Connecting to running Chrome via CDP at {cdp_url} for StepStone...")
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            print(f"[!] Could not connect to Chrome on {cdp_url}: {e}")
            print(
                'Ensure Chrome is running with:\n'
                '/Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome '
                '--remote-debugging-port=9222 --user-data-dir="$HOME/stepstone_clean_profile" &'
            )
            return []

        default_context = browser.contexts[0]
        page = (
            default_context.pages[0]
            if default_context.pages
            else await default_context.new_page()
        )

        encoded_kw = quote_plus(keyword)
        encoded_loc = quote_plus(location)

        for page_num in range(1, max_pages + 1):
            if page_num == 1:
                url = f"https://www.stepstone.de/jobs/{encoded_kw}/in-{encoded_loc}?sort=2"  # sort=2: newest first
            else:
                url = f"https://www.stepstone.de/jobs/{encoded_kw}/in-{encoded_loc}?page={page_num}&sort=2"

            print(f"\n--- Scraping StepStone Page {page_num}/{max_pages} ---")
            print(f"Navigating to {url}...")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                print(f"[!] Warning: StepStone navigation error: {e}")

            # Handle cookie banner
            if page_num == 1:
                await asyncio.sleep(2)
                try:
                    cookie_btn = page.locator(
                        "#onetrust-accept-btn-handler, button[data-testid='uc-accept-all-button'], button:has-text('Alle akzeptieren')"
                    ).first
                    if await cookie_btn.count() > 0 and await cookie_btn.is_visible():
                        await cookie_btn.click()
                        await asyncio.sleep(1)
                except Exception:
                    pass

            # Scroll down smoothly to hydrate lazy-loaded cards
            print("Hydrating StepStone job cards...")
            for _ in range(5):
                await page.mouse.wheel(0, 800)
                await asyncio.sleep(0.7)

            articles = await page.locator("article").all()
            print(f"Discovered {len(articles)} article nodes on page {page_num}...")

            for article in articles:
                # Target the job title link inside the article
                link_locator = article.locator(
                    "a[data-genesis-element='BASE_BLOCK'][href*='/stellenangebote-'], "
                    "a[href*='/stellenangebote-'], a[data-genesis-element='CARD_TITLE_LINK']"
                ).first

                if await link_locator.count() == 0:
                    link_locator = article.locator("a[href*='/stellenangebote--']").first
                    if await link_locator.count() == 0:
                        continue

                raw_href = await link_locator.get_attribute("href")
                if not raw_href:
                    continue

                clean_url = urljoin("https://www.stepstone.de", raw_href.split("?")[0])

                job_id = extract_stepstone_job_id(clean_url)
                if not job_id:
                    # Fallback to URL hash
                    job_id = str(abs(hash(clean_url)))

                if job_id in seen_keys or job_id in newly_seen_keys:
                    continue

                # Extract Title
                title = ""
                title_elem = article.locator(
                    "h2, [data-genesis-element='CARD_TITLE_LINK']"
                ).first
                if await title_elem.count() > 0:
                    title = (await title_elem.inner_text()).strip()
                if not title:
                    title = (await link_locator.inner_text()).strip()

                if not title or len(title) < 3:
                    continue

                title = title.split("\n")[0].strip()

                # Extract Company Name
                company = "N/A"
                company_elem = article.locator(
                    "[data-genesis-element='COMPANY_NAME'], span[class*='Company'], div[class*='Company']"
                ).first
                if await company_elem.count() > 0 and await company_elem.is_visible():
                    company = (await company_elem.inner_text()).strip()
                else:
                    raw_text = await article.inner_text()
                    lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
                    for idx_line, line in enumerate(lines):
                        if line == title and idx_line + 1 < len(lines):
                            candidate = lines[idx_line + 1]
                            if len(candidate) > 2 and not re.search(
                                r"(home-office|remote|vollzeit|teilzeit|vor \d+|neu)",
                                candidate,
                                re.I,
                            ):
                                company = candidate
                                break

                # Extract Location
                job_location = location
                loc_elem = article.locator(
                    "[data-genesis-element='METADATA_ITEM_LOCATION'], span[class*='Location']"
                ).first
                if await loc_elem.count() > 0 and await loc_elem.is_visible():
                    job_location = (await loc_elem.inner_text()).strip()

                # Extract Date
                published_str = await extract_date_from_article(article)

                # 24-hour filter check
                if filter_24h and not is_within_24h(published_str, source="stepstone"):
                    continue

                newly_seen_keys.add(job_id)
                jobs.append({
                    "job_id": job_id,
                    "source": "stepstone",
                    "job_title": title,
                    "company_name": company,
                    "location": job_location,
                    "published": published_str,
                    "job_url": clean_url,
                    "full_description": ""
                })

        print(f"\nSuccessfully collected {len(jobs)} StepStone jobs:\n")
        for idx, j in enumerate(jobs, 1):
            print(f"[{idx}] {j['job_title']}")
            print(f"    Company:  {j['company_name']}")
            print(f"    Location: {j['location']}")
            print(f"    Date:     {j['published']}")
            print(f"    URL:      {j['job_url']}\n")

        # Save output JSON
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(jobs, f, ensure_ascii=False, indent=2)

        record_seen_keys(newly_seen_keys)
        print(f"Saved {len(jobs)} jobs to '{output_path}' and updated '{SEEN_KEYS_FILE}'.")

    return jobs


if __name__ == "__main__":
    asyncio.run(scrape_stepstone_cdp())