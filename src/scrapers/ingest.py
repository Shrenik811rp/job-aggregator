"""Data ingestion, ATS detection, filtering, and tiered extraction engine."""
import html
import logging
import random
import re
import time
from typing import Dict, List, Optional, Set
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import feedparser
import requests

logger = logging.getLogger(__name__)

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

GITHUB_MARKDOWN_URLS = [
    "https://raw.githubusercontent.com/jobright-ai/2026-Software-Engineer-New-Grad/main/README.md",
    "https://raw.githubusercontent.com/jobright-ai/2026-Data-Analysis-New-Grad/main/README.md",
    "https://raw.githubusercontent.com/jobright-ai/2026-Data-Analysis-Internship/main/README.md",
    "https://raw.githubusercontent.com/jobright-ai/2026-Software-Engineer-Internship/main/README.md",
    "https://raw.githubusercontent.com/jobright-ai/2026-Product-Management-Internship/main/README.md",
    "https://raw.githubusercontent.com/jobright-ai/2026-Management-New-Grad/main/README.md",
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md",
    "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/README.md",
    "https://raw.githubusercontent.com/speedyapply/2027-AI-College-Jobs/main/README.md",
    "https://raw.githubusercontent.com/zapplyjobs/Internships-2027/main/README.md",
]

TEXAS_KEYWORDS = [
    "dallas",
    "richardson",
    "austin",
    "houston",
    "plano",
    "irving",
    "fort worth",
    "texas",
    "tx",
]

REMOTE_KEYWORDS = [
    "remote",
    "nationwide",
    "anywhere",
    "telecommute",
    "virtual",
    "wfh",
    "work from home",
    "worldwide",
]

NON_US_KEYWORDS = [
    "uk", "united kingdom", "london", "england", "scotland",
    "india", "bengaluru", "bangalore", "mumbai", "hyderabad", "delhi", "pune", "chennai", "gurgaon", "noida",
    "germany", "berlin", "munich", "frankfurt", "hamburg",
    "canada", "toronto", "vancouver", "montreal", "ontario", "quebec", "ottawa", "calgary",
    "australia", "sydney", "melbourne", "brisbane",
    "france", "paris",
    "ireland", "dublin",
    "netherlands", "amsterdam",
    "poland", "warsaw", "krakow",
    "spain", "madrid", "barcelona",
    "brazil", "sao paulo",
    "singapore", "japan", "tokyo",
    "china", "beijing", "shanghai", "shenzhen",
    "switzerland", "zurich",
    "sweden", "stockholm",
]

EXCLUDED_TITLE_KEYWORDS = ["marketing", "sales analyst"]

ACCEPTED_TITLE_KEYWORDS = [
    "intern",
    "trainee",
    "co-op",
    "coop",
    "apprentice",
    "devops",
    "sre",
    "reliability",
    "cloud",
    "data analyst",
    "price analyst",
    "business analyst",
    "data engineer",
    "product manager",
    "product development",
    "mlops",
    "dataops",
    "technical sales",
    "software",
    "swe",
]

SENIORITY_EXCLUDE_REGEX = re.compile(r'(?:[5-9]|1[0-9])\+?\s*(?:years?|yrs?)', re.IGNORECASE)


def _clean_markdown(text: str) -> str:
    """Strip markdown formatting and HTML tags from table cell content."""
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'[*_~`]', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def _extract_url(text: str) -> str:
    """Extract standard HTTP(S) URL from text containing markdown or HTML."""
    md_match = re.search(r'\[.*?\]\((https?://[^\s\)]+)\)', text)
    if md_match:
        return md_match.group(1).strip()

    html_match = re.search(r'href=["\'](https?://[^"\'\s]+)["\']', text, re.IGNORECASE)
    if html_match:
        return html_match.group(1).strip()

    bare_match = re.search(r'(https?://[^\s\|<>\)]+)', text)
    if bare_match:
        return bare_match.group(1).strip()

    return ""


def parse_markdown_tables(markdown_text: str) -> List[Dict[str, str]]:
    """Parse GitHub markdown table rows into a structured list of job dicts."""
    jobs: List[Dict[str, str]] = []
    lines = markdown_text.splitlines()

    in_table = False
    company_idx = 0
    role_idx = 1
    location_idx = 2
    link_idx = 3

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|") and not stripped.endswith("|"):
            in_table = False
            continue

        raw_cells = stripped.split("|")
        if len(raw_cells) > 1 and raw_cells[0].strip() == "":
            raw_cells = raw_cells[1:]
        if len(raw_cells) > 1 and raw_cells[-1].strip() == "":
            raw_cells = raw_cells[:-1]

        cells = [c.strip() for c in raw_cells]
        if not cells or len(cells) < 2:
            continue

        # Skip table divider rows
        if all(re.match(r"^[\s:\-]+$", c) for c in cells):
            in_table = True
            continue

        # Detect table header row
        header_candidate = [c.lower() for c in cells]
        if (
            any("company" in c or "organization" in c for c in header_candidate)
            and any("role" in c or "title" in c or "position" in c or "job" in c for c in header_candidate)
        ):
            in_table = True
            for i, h in enumerate(header_candidate):
                if "company" in h or "organization" in h or "name" in h:
                    company_idx = i
                elif "role" in h or "title" in h or "position" in h or "job" in h:
                    role_idx = i
                elif "location" in h or "place" in h or "city" in h:
                    location_idx = i
                elif "link" in h or "apply" in h or "app" in h or "posting" in h or "url" in h:
                    link_idx = i
            continue

        if not in_table and len(cells) < 3:
            continue

        company = _clean_markdown(cells[company_idx]) if company_idx < len(cells) else ""
        role = _clean_markdown(cells[role_idx]) if role_idx < len(cells) else ""
        location = _clean_markdown(cells[location_idx]) if location_idx < len(cells) else ""

        url = ""
        if link_idx < len(cells):
            url = _extract_url(cells[link_idx])
        if not url:
            for c in cells:
                candidate_url = _extract_url(c)
                if candidate_url:
                    url = candidate_url
                    break

        if role and url:
            jobs.append({
                "title": role,
                "company": company,
                "location": location,
                "url": url,
            })

    return jobs


def fetch_raw_jobs() -> List[Dict[str, str]]:
    """Query GitHub markdowns, We Work Remotely RSS, Arbeitnow API, and RemoteOK API.

    Each source executes in an independent try/except block to ensure resilience.
    Returns a unified list of raw job dictionaries.
    """
    raw_jobs: List[Dict[str, str]] = []

    # 1. GitHub Markdown Repositories
    for url in GITHUB_MARKDOWN_URLS:
        try:
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=10)
            if resp.status_code == 200:
                parsed = parse_markdown_tables(resp.text)
                raw_jobs.extend(parsed)
                logger.info("Fetched %d jobs from GitHub markdown: %s", len(parsed), url)
            else:
                logger.warning("GitHub URL returned status %d: %s", resp.status_code, url)
        except Exception as e:
            logger.warning("Failed to fetch GitHub markdown from %s: %s", url, e)

    # 2. We Work Remotely RSS
    try:
        wwr_url = "https://weworkremotely.com/remote-jobs.rss"
        resp = requests.get(wwr_url, headers=REQUEST_HEADERS, timeout=10)
        if resp.status_code == 200:
            feed = feedparser.parse(resp.text)
            count = 0
            for entry in feed.entries:
                raw_title = entry.get("title", "")
                if ":" in raw_title:
                    company, title = [part.strip() for part in raw_title.split(":", 1)]
                else:
                    company = entry.get("author", "WeWorkRemotely")
                    title = raw_title.strip()

                link = entry.get("link", "")
                region = entry.get("region", "") or "Remote"
                if title and link:
                    raw_jobs.append({
                        "title": title,
                        "company": company,
                        "location": region,
                        "url": link,
                    })
                    count += 1
            logger.info("Fetched %d jobs from We Work Remotely RSS", count)
    except Exception as e:
        logger.warning("Failed to fetch We Work Remotely RSS: %s", e)

    # 3. Arbeitnow API
    try:
        an_url = "https://www.arbeitnow.com/api/job-board-api"
        resp = requests.get(an_url, headers=REQUEST_HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("data", [])
            count = 0
            for item in items:
                title = item.get("title", "")
                company = item.get("company_name", "")
                location = item.get("location", "")
                link = item.get("url", "")
                if title and link:
                    raw_jobs.append({
                        "title": title,
                        "company": company,
                        "location": location,
                        "url": link,
                    })
                    count += 1
            logger.info("Fetched %d jobs from Arbeitnow API", count)
    except Exception as e:
        logger.warning("Failed to fetch Arbeitnow API: %s", e)

    # 4. RemoteOK API
    try:
        rok_url = "https://remoteok.com/api"
        resp = requests.get(rok_url, headers=REQUEST_HEADERS, timeout=10)
        if resp.status_code == 200:
            items = resp.json()
            count = 0
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    title = item.get("position", "")
                    company = item.get("company", "")
                    location = item.get("location", "") or "Remote"
                    link = item.get("url", "") or item.get("apply_url", "")
                    if title and link:
                        raw_jobs.append({
                            "title": title,
                            "company": company,
                            "location": location,
                            "url": link,
                        })
                        count += 1
            logger.info("Fetched %d jobs from RemoteOK API", count)
    except Exception as e:
        logger.warning("Failed to fetch RemoteOK API: %s", e)

    return raw_jobs


def is_valid_role(title: str) -> bool:
    """Check if title matches required technical roles and excludes forbidden keywords."""
    if not title:
        return False
    title_lower = title.lower().strip()

    # Exclusions
    for exc in EXCLUDED_TITLE_KEYWORDS:
        if exc in title_lower:
            return False

    # Accepted matches
    for kw in ACCEPTED_TITLE_KEYWORDS:
        if len(kw) <= 4:
            if re.search(rf'\b{re.escape(kw)}\b', title_lower):
                return True
        elif kw in title_lower:
            return True

    # Any role ending with 'analyst'
    if re.search(r'\banalyst\b\s*$', title_lower):
        return True

    return False


def is_excessive_seniority(text: str) -> bool:
    """Return True if text explicitly demands >4 years of experience."""
    if not text:
        return False
    return bool(SENIORITY_EXCLUDE_REGEX.search(text))


def get_location_rank(location: str) -> Optional[int]:
    """Classify location priority:
    Rank 1: Texas locations (Dallas, Richardson, Austin, Houston, Plano, Irving, Fort Worth, TX)
    Rank 2: Remote (US Remote, Nationwide Remote, Worldwide Remote, Anywhere, WFH)
    Rank 3: All other US locations (CA, NY, WA, IL, MA, GA, etc. or unspecified/USA)
    None: Discard non-US listings unless marked Remote/Worldwide
    """
    if not location:
        return 3

    loc_lower = location.lower().strip()

    # 1. Texas check (Highest priority: Texas first)
    for kw in TEXAS_KEYWORDS:
        if kw == "tx":
            if re.search(r'\btx\b', loc_lower):
                return 1
        elif kw in loc_lower:
            return 1

    # 2. Remote check (Second priority)
    is_remote = False
    for kw in REMOTE_KEYWORDS:
        if kw in loc_lower:
            is_remote = True
            break

    # 3. Non-US check
    is_non_us = False
    for kw in NON_US_KEYWORDS:
        if len(kw) <= 3:
            if re.search(rf'\b{re.escape(kw)}\b', loc_lower):
                is_non_us = True
                break
        elif kw in loc_lower:
            is_non_us = True
            break

    # Discard explicitly non-US jobs unless marked Remote/Worldwide
    if is_non_us and not is_remote:
        return None

    if is_remote:
        return 2

    # 4. All other US locations (CA, NY, WA, etc.)
    return 3


def filter_and_sort_jobs(raw_jobs: List[Dict[str, str]], existing_links: Set[str]) -> List[Dict[str, str]]:
    """Deduplicate against existing links, filter by role/seniority, and sort by location priority.

    Accepts all jobs located anywhere in the United States or marked as Remote.
    Sorting priority:
      1. Texas (TX, Dallas, Austin, etc.)
      2. Remote (US Remote, Anywhere)
      3. All other US locations (CA, NY, WA, etc.)
    """
    seen_urls = set(existing_links)
    filtered_jobs: List[Dict[str, str]] = []

    for job in raw_jobs:
        url = job.get("url", "").strip()
        if not url or url in seen_urls:
            continue

        title = job.get("title", "").strip()
        if not is_valid_role(title):
            continue

        desc = job.get("description", "")
        if is_excessive_seniority(title) or is_excessive_seniority(desc):
            continue

        loc = job.get("location", "")
        rank = get_location_rank(loc)
        if rank is None:
            continue

        seen_urls.add(url)
        job_item = dict(job)
        job_item["_rank"] = rank
        filtered_jobs.append(job_item)

    # Sort strictly by priority rank (1: Texas, 2: Remote, 3: Other US)
    filtered_jobs.sort(key=lambda j: j["_rank"])

    for j in filtered_jobs:
        j.pop("_rank", None)

    return filtered_jobs


def detect_ats(url: str) -> str:
    """Map URL domain footprints to ATS platform identifiers."""
    if not url:
        return "generic"
    url_lower = url.lower()

    if "boards.greenhouse.io" in url_lower or "job-boards.greenhouse.io" in url_lower:
        return "greenhouse"
    if "jobs.lever.co" in url_lower:
        return "lever"
    if "jobs.ashbyhq.com" in url_lower or "ashbyhq.com" in url_lower:
        return "ashby"
    if "myworkdayjobs.com" in url_lower:
        return "workday"
    if "icims.com" in url_lower:
        return "icims"
    if "taleo.net" in url_lower or "oraclecloud.com" in url_lower:
        return "taleo"
    if "smartrecruiters.com" in url_lower:
        return "smartrecruiters"
    if "bamboohr.com" in url_lower:
        return "bamboohr"

    return "generic"


BOT_CHALLENGE_MARKERS = [
    "cf-browser-verification",
    "challenges.cloudflare.com",
    "Attention Required! | Cloudflare",
    "verifies you are not a bot",
    "Just a moment...",
    "Cloudflare Ray ID",
]


class BotChallengeException(Exception):
    """Raised when an anti-bot challenge or Cloudflare gate is detected."""
    pass


def _check_bot_challenge(text: str) -> None:
    """Check text for anti-bot challenge markers and raise BotChallengeException if found."""
    if not text:
        return
    text_lower = text.lower()
    for marker in BOT_CHALLENGE_MARKERS:
        if marker.lower() in text_lower:
            raise BotChallengeException(f"Bot challenge detected with marker: '{marker}'")


def _extract_workday_cxs_tier1(url: str) -> Optional[str]:
    """Extract job description via Workday Candidate Experience Site (CXS) REST API."""
    try:
        pattern = re.compile(
            r"https://([^.]+)(?:\.([^.]+))?\.myworkdayjobs\.com/(?:[a-zA-Z-]+/)?([^/]+)(/job/.*)",
            re.IGNORECASE,
        )
        m = pattern.search(url)
        if not m:
            return None

        tenant = m.group(1)
        pod = m.group(2) or ""
        site = m.group(3)
        external_path = m.group(4).split("?")[0].rstrip("/")

        host = f"{tenant}.{pod}.myworkdayjobs.com" if pod else f"{tenant}.myworkdayjobs.com"
        cxs_url = f"https://{host}/wday/cxs/{tenant}/{site}{external_path}"

        headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": REQUEST_HEADERS["User-Agent"],
        }

        resp = requests.get(cxs_url, headers=headers, timeout=10)
        if resp.status_code == 200:
            payload = resp.json()
            raw_html = payload.get("jobPostingInfo", {}).get("jobDescription", "")
            if raw_html:
                soup = BeautifulSoup(raw_html, "html.parser")
                text = soup.get_text(separator="\n").strip()
                if len(text) >= 50:
                    return text
    except Exception as e:
        logger.debug("Workday CXS Tier 1 extraction failed for %s: %s", url, e)
    return None


def _extract_greenhouse_tier1(url: str) -> Optional[str]:
    """Extract job description via Greenhouse Public Board API."""
    try:
        board_match = re.search(r'greenhouse\.io/(?:embed/job_app\?for=)?([^/?#]+)/jobs/(\d+)', url)
        board = ""
        job_id = ""

        if board_match:
            board = board_match.group(1)
            job_id = board_match.group(2)
        else:
            parsed = urlparse(url)
            query = parse_qs(parsed.query)
            if "gh_jid" in query and query["gh_jid"]:
                job_id = query["gh_jid"][0]
                path_parts = [p for p in parsed.path.split("/") if p]
                if path_parts:
                    board = path_parts[0]

        if not board or not job_id:
            return None

        api_url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}"
        resp = requests.get(api_url, headers=REQUEST_HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            content_html = data.get("content", "")
            if content_html:
                unescaped = html.unescape(content_html)
                soup = BeautifulSoup(unescaped, "html.parser")
                text = soup.get_text(separator="\n").strip()
                if len(text) >= 50:
                    return text
    except Exception as e:
        logger.debug("Greenhouse Tier 1 extraction failed: %s", e)
    return None


def _extract_lever_tier1(url: str) -> Optional[str]:
    """Extract job description via Lever Public Postings API."""
    try:
        match = re.search(r'lever\.co/([^/?#]+)/([a-f0-9\-]+)', url)
        if not match:
            return None

        company = match.group(1)
        job_id = match.group(2)

        api_url = f"https://api.lever.co/v0/postings/{company}/{job_id}?mode=json"
        resp = requests.get(api_url, headers=REQUEST_HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            desc = data.get("descriptionPlain", "")
            if not desc:
                desc_html = data.get("description", "")
                if desc_html:
                    desc = BeautifulSoup(desc_html, "html.parser").get_text(separator="\n").strip()

            for item in data.get("lists", []):
                list_title = item.get("text", "")
                list_content = item.get("content", "")
                clean_content = BeautifulSoup(list_content, "html.parser").get_text(separator="\n").strip()
                desc += f"\n\n{list_title}\n{clean_content}"

            if desc and len(desc.strip()) >= 50:
                return desc.strip()
    except Exception as e:
        logger.debug("Lever Tier 1 extraction failed: %s", e)
    return None


def _extract_ashby_tier1(url: str) -> Optional[str]:
    """Extract job description via Ashby Public Job Board API."""
    try:
        match = re.search(r'ashbyhq\.com/([^/?#]+)(?:/([a-f0-9\-]+))?', url)
        if not match:
            return None

        company = match.group(1)
        target_id = match.group(2)

        api_url = f"https://api.ashbyhq.com/posting-api/job-board/{company}"
        resp = requests.get(api_url, headers=REQUEST_HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            jobs = data.get("jobs", [])
            for job in jobs:
                job_url = job.get("jobUrl", "")
                job_id = job.get("id", "")
                if (target_id and job_id == target_id) or (job_url and job_url == url):
                    desc = job.get("descriptionPlain", "")
                    if not desc:
                        desc_html = job.get("descriptionHtml", "")
                        if desc_html:
                            desc = BeautifulSoup(desc_html, "html.parser").get_text(separator="\n").strip()
                    if desc and len(desc.strip()) >= 50:
                        return desc.strip()
    except Exception as e:
        logger.debug("Ashby Tier 1 extraction failed: %s", e)
    return None


def _extract_tier2_soup(url: str) -> Optional[str]:
    """Extract job description via standard requests + BeautifulSoup parsing.

    Raises BotChallengeException if anti-bot challenge is detected.
    """
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=10)
    if resp.status_code == 200:
        # Check for bot challenge in raw response
        _check_bot_challenge(resp.text)

        soup = BeautifulSoup(resp.text, "html.parser")
        for elem in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            elem.extract()

        # Target standard job description containers
        candidate = (
            soup.find("main")
            or soup.find("div", class_=re.compile(r"job-?description|posting-content", re.I))
            or soup.find("article")
            or soup.select_one("[class*='description']")
            or soup.select_one("[id*='description']")
            or soup.select_one("[class*='jobDescription']")
            or soup.select_one("[class*='posting-content']")
        )
        if candidate:
            text = candidate.get_text(separator="\n").strip()
            _check_bot_challenge(text)
            if len(text) >= 50:
                return text

        if soup.body:
            text = soup.body.get_text(separator="\n").strip()
            _check_bot_challenge(text)
            if len(text) >= 50:
                return text
    elif resp.status_code in (403, 503):
        _check_bot_challenge(resp.text)

    return None


def _extract_tier3_playwright(url: str, ats_type: str = "") -> Optional[str]:
    """Extract job description via headless Chromium for dynamic JS-rendered sites.

    - Supports iCIMS multi-frame inspection.
    - Aborts and raises BotChallengeException if a challenge screen is detected.
    """
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, timeout=15000, wait_until="domcontentloaded")
                page.wait_for_timeout(2000)

                # Check page content for bot challenge
                content = page.content()
                _check_bot_challenge(content)

                main_text = page.evaluate("document.body.innerText") or ""
                _check_bot_challenge(main_text)

                # If ats_type is icims or main body is unusually short, inspect child frames
                if ats_type == "icims" or len(main_text.strip()) < 150:
                    child_frames = [f for f in page.frames if f != getattr(page, "main_frame", None)]
                    for frame in child_frames:
                        try:
                            frame_text = frame.evaluate("document.body.innerText") or ""
                            _check_bot_challenge(frame_text)
                            if frame_text and len(frame_text.strip()) >= 50:
                                return frame_text.strip()
                        except BotChallengeException:
                            raise
                        except Exception:
                            continue

                if main_text and len(main_text.strip()) >= 50:
                    return main_text.strip()
            finally:
                browser.close()
    except BotChallengeException:
        raise
    except Exception as e:
        logger.warning("Tier 3 Playwright extraction failed for %s: %s", url, e)
    return None


def extract_job_description(url: str, ats_type: str = "", enable_delay: bool = True) -> str:
    """Extract job description using API-first tiered strategy:
    Tier 1: Direct ATS Backend JSON APIs (Workday CXS, Greenhouse, Lever, Ashby)
    Tier 2: Requests + BeautifulSoup HTML parser with bot-challenge detection
    Tier 3: Playwright Headless Browser fallback
    Emergency Fallback: "Unable to extract job listing"
    """
    if enable_delay:
        time.sleep(random.uniform(1.5, 3.5))

    if not ats_type:
        ats_type = detect_ats(url)

    try:
        # --- Tier 1: Direct ATS Backend JSON APIs ---
        if ats_type == "workday" or "myworkdayjobs.com" in url.lower():
            result = _extract_workday_cxs_tier1(url)
            if result and len(result.strip()) >= 50:
                return result.strip()
        elif ats_type == "greenhouse" or "greenhouse.io" in url.lower():
            result = _extract_greenhouse_tier1(url)
            if result and len(result.strip()) >= 50:
                return result.strip()
        elif ats_type == "lever" or "lever.co" in url.lower():
            result = _extract_lever_tier1(url)
            if result and len(result.strip()) >= 50:
                return result.strip()
        elif ats_type == "ashby" or "ashbyhq.com" in url.lower():
            result = _extract_ashby_tier1(url)
            if result and len(result.strip()) >= 50:
                return result.strip()

        # --- Tier 2: Requests + BeautifulSoup with Bot-Challenge Detection ---
        try:
            result = _extract_tier2_soup(url)
            if result and len(result.strip()) >= 50:
                return result.strip()
        except BotChallengeException as bce:
            logger.warning("Bot challenge detected on %s during Tier 2. Aborting extraction: %s", url, bce)
            return "Unable to extract job listing"
        except Exception as e:
            logger.debug("Tier 2 extraction failed for %s: %s", url, e)

        # --- Tier 3: Playwright Headless Fallback ---
        try:
            result = _extract_tier3_playwright(url, ats_type=ats_type)
            if result and len(result.strip()) >= 50:
                return result.strip()
        except BotChallengeException as bce:
            logger.warning("Bot challenge detected on %s during Tier 3. Aborting extraction: %s", url, bce)
            return "Unable to extract job listing"
        except Exception as e:
            logger.warning("Tier 3 Playwright extraction failed for %s: %s", url, e)

    except Exception as e:
        logger.warning("Unexpected error during job extraction for %s: %s", url, e)

    # --- Guardrail Fallback ---
    logger.warning("All extraction tiers failed for %s. Employing guardrail fallback.", url)
    return "Unable to extract job listing"
