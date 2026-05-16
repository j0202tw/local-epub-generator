import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from . import utils

logger = logging.getLogger(__name__)

# Patterns to extract chapter numbers from titles
CHAPTER_NUM_PATTERNS = [
    re.compile(r"第\s*(\d+)\s*[章回节話話節巻卷部]"),  # 第1章, 第 2 章, 第3回, 第4卷
    re.compile(r"(?:chapter|ch|part|vol|section)\s*[#:.：]?\s*(\d+)", re.I),  # Chapter 1, Ch.2, Part 3
    re.compile(r"(\d+)\s*[\.、．\s]"),  # 1., 2、
    re.compile(r"^(\d+)$"),  # standalone number
]


def extract_chapter_num(title):
    """Try to extract a chapter number from a title string.

    Returns the number or None if not found.
    """
    for pattern in CHAPTER_NUM_PATTERNS:
        match = pattern.search(title)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return None


def sort_chapters(chapters):
    """Sort chapters by extracted chapter number when possible.

    Chapters that have an identifiable number are sorted numerically.
    Chapters without a number retain their relative order at the end.
    """
    numbered = []
    unnumbered = []

    for ch in chapters:
        num = extract_chapter_num(ch["title"])
        if num is not None:
            numbered.append((num, ch))
        else:
            unnumbered.append(ch)

    numbered.sort(key=lambda x: x[0])
    sorted_chapters = [ch for _, ch in numbered] + unnumbered
    return sorted_chapters


def parse_catalog(url, selector=None, session=None, auto_sort=True):
    """Parse a catalog/TOC page and extract chapter links.

    Args:
        url: The catalog page URL.
        selector: Optional CSS selector for chapter links (e.g. 'ul.chapter-list a').
        session: Optional requests session.

    Returns:
        List of dicts: [{"title": str, "url": str}, ...]
    """
    logger.info("Parsing catalog: %s", url)
    html, _, _ = utils.download_page(url, session=session)
    soup = BeautifulSoup(html, "lxml")

    chapters = []
    seen_urls = set()

    if selector:
        links = soup.select(selector)
    else:
        # Heuristic: find all <a> tags that look like chapter links
        links = soup.find_all("a")

    for a in links:
        href = a.get("href", "").strip()
        title = a.get_text(strip=True)

        if not href or not title:
            continue
        if len(title) < 2:
            continue
        if href.startswith("javascript:") or href.startswith("#"):
            continue

        full_url = urljoin(url, href)
        # Remove fragment for dedup
        clean_url = full_url.split("#")[0]
        if clean_url in seen_urls:
            continue

        chapters.append({"title": title, "url": clean_url})
        seen_urls.add(clean_url)

    if auto_sort:
        chapters = sort_chapters(chapters)

    logger.info("Found %d chapters", len(chapters))
    return chapters
