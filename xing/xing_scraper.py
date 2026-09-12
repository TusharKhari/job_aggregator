import asyncio
import json
import re
from pathlib import Path
from urllib.parse import quote_plus, urljoin
import sys
# pyrefly: ignore [missing-import]
from playwright.async_api import async_playwright

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils import is_within_24h

DEFAULT_OUTPUT_JSON = SCRIPT_DIR / "xing_agent_jobs.json"
SEEN_KEYS_FILE = SCRIPT_DIR / "seen_xing_keys.txt"

MAX_PAGES = 2
ITEMS_PER_PAGE = 20  # Xing uses 0-indexed offsets in increments of 20

# Badges and UI labels that are NOT company names
IGNORED_BADGE_KEYWORDS = {
    "urgently hiring",
    "dringend gesucht",
    "be an early applicant",
    "sei einer der ersten",
    "frühe bewerbung",
    "top-arbeitgeber",
    "top arbeitgeber",
    "schnellbewerbung",
    "quick apply",
    "promoted",
    "gesponsert",
    "anzeige",
    "empfohlen",
    "neu",
    "vor",
    "tag",
    "stunde",
    "minute",
    "remote",
    "homeoffice",
    "hybrid",
    "vollzeit",
    "teilzeit",
    "befristet",
    "unbefristet",
    "bewerben",
    "jetzt bewerben",
    "post job",
    "stellenangebote",
    "weitere angebote",
    "jobs directory",
    "main sections",
}


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


def extract_xing_job_id(url: str) -> str | None:
    """Extracts numeric job ID from a Xing job URL (e.g. ...-157321265)."""
    match = re.search(r"-(\d+)(?:[/?#]|$)", url)
    return match.group(1) if match else None


def is_badge_or_meta(text: str) -> bool:
    """Checks if a string looks like a status badge, date, or metadata rather than a company."""
    t_lower = text.lower().strip()
    if len(t_lower) < 2:
        return True
    for kw in IGNORED_BADGE_KEYWORDS:
        if kw in t_lower:
            return True
    return False


async def scrape_xing_cdp(
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
        print(f"Connecting to running Chrome via CDP at {cdp_url}...")
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            print(f"[!] Could not connect to Chrome on {cdp_url}: {e}")
            print(
                'Ensure Chrome is running with:\n'
                '/Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome '
                '--remote-debugging-port=9222 --user-data-dir="$HOME/xing_clean_profile" &'
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
            offset = (page_num - 1) * ITEMS_PER_PAGE
            url = f"https://www.xing.com/jobs/search?keywords={encoded_kw}&location={encoded_loc}&sort=DATE_DESC&offset={offset}"

            print(f"\n--- Scraping Xing Page {page_num}/{max_pages} ---")
            print(f"Navigating to {url}...")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                print(f"[!] Warning: Page navigation timed out or error: {e}")

            # Accept Cookie Banner on first page visit if present
            if page_num == 1:
                await asyncio.sleep(2)
                try:
                    await page.evaluate("""
                        () => {
                            const host = document.querySelector('#usercentrics-root');
                            if (host && host.shadowRoot) {
                                const btn = host.shadowRoot.querySelector('button[data-testid="uc-accept-all-button"]');
                                if (btn) btn.click();
                            }
                            const normalBtn = document.querySelector('#uc-btn-accept-banner');
                            if (normalBtn) normalBtn.click();
                        }
                    """)
                    await asyncio.sleep(1)
                except Exception:
                    pass

            # Scroll to hydrate dynamic job cards
            print("Hydrating job cards...")
            for _ in range(4):
                await page.mouse.wheel(0, 700)
                await asyncio.sleep(0.8)

            # Find all links on page
            links = await page.locator("a[href*='/jobs/']").all()
            print(f"Discovered {len(links)} potential job link elements on page {page_num}...")

            for link in links:
                raw_href = await link.get_attribute("href")
                if not raw_href:
                    continue

                clean_url = urljoin("https://www.xing.com", raw_href.split("?")[0].split("#")[0])

                # Validate genuine Xing job listing URL format (ends with -<numeric_id>)
                job_id = extract_xing_job_id(clean_url)
                if not job_id:
                    continue

                if job_id in seen_keys or job_id in newly_seen_keys:
                    continue

                # Extract title
                title = (await link.inner_text()).strip()
                if not title:
                    heading = link.locator("h2, h3, span").first
                    if await heading.count() > 0:
                        title = (await heading.inner_text()).strip()
                if not title:
                    aria = await link.get_attribute("aria-label")
                    if aria:
                        title = aria.strip()

                # Locate card container
                card = link.locator(
                    "xpath=./ancestor::article | ./ancestor::li | ./ancestor::div[contains(@class, 'card')]"
                ).first

                if (not title or len(title) < 3) and await card.count() > 0:
                    card_h = card.locator("h2, h3").first
                    if await card_h.count() > 0:
                        title = (await card_h.inner_text()).strip()

                if not title or len(title) < 3:
                    continue

                title = title.split("\n")[0].strip()

                # Discard non-job headings
                if title.lower() in IGNORED_BADGE_KEYWORDS:
                    continue

                # Extract company
                company = "N/A"
                location_str = location
                published_str = "N/A"

                if await card.count() > 0:
                    # 1. Try explicit company selector first
                    comp_elem = card.locator(
                        "a[href*='/companies/'], p[data-testid*='company'], span[data-testid*='company'], "
                        "p[class*='company'], span[class*='company']"
                    ).first
                    if await comp_elem.count() > 0:
                        candidate_comp = (await comp_elem.inner_text()).strip()
                        if candidate_comp and not is_badge_or_meta(candidate_comp):
                            company = candidate_comp

                    # 2. Fallback to line scanning excluding title and badges
                    card_text = await card.inner_text()
                    lines = [line.strip() for line in card_text.split("\n") if line.strip()]

                    if company == "N/A":
                        for line in lines:
                            if line != title and not is_badge_or_meta(line):
                                company = line
                                break

                    # Look for date info in card text
                    for line in lines:
                        l_lower = line.lower()
                        if any(term in l_lower for term in ["vor ", "gestern", "heute"]):
                            published_str = line
                            break

                # Apply strict 24-hour filter
                if filter_24h and not is_within_24h(published_str, source="xing"):
                    continue

                newly_seen_keys.add(job_id)
                jobs.append({
                    "job_id": job_id,
                    "source": "xing",
                    "job_title": title,
                    "company_name": company,
                    "location": location_str,
                    "published": published_str,
                    "job_url": clean_url,
                    "full_description": ""
                })

        print(f"\nSuccessfully collected {len(jobs)} new Xing jobs:")
        for idx, j in enumerate(jobs, 1):
            print(f"[{idx}] {j['job_title']}")
            print(f"    Company: {j['company_name']}")
            print(f"    URL:     {j['job_url']}\n")

        # Save output JSON
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(jobs, f, ensure_ascii=False, indent=2)

        record_seen_keys(newly_seen_keys)
        print(f"Saved {len(jobs)} jobs to '{output_path}' and updated '{SEEN_KEYS_FILE}'.")

    return jobs


if __name__ == "__main__":
    asyncio.run(scrape_xing_cdp())