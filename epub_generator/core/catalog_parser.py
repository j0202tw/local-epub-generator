import logging
import re
from urllib.parse import urljoin, urlparse

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

# Common navigation/non-chapter title patterns to filter out
NAV_TITLES = re.compile(
    r"^(首頁|主頁|下一章|上一章|目錄|返回|首页|主页|关于|關於|联系|聯繫|"
    r"登錄|登录|注册|註冊|搜索|收藏|推薦|推荐|設置|设置|幫助|帮助|"
    r"copyright|about|privacy|contact|home|bookmark|share|"
    r"更多|more|閱讀全文|阅读全文)$", re.I
)


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


def parse_catalog(url, selector=None, session=None, auto_sort=True, url_pattern=None):
    """Parse a catalog/TOC page and extract chapter links.

    Args:
        url: The catalog page URL.
        selector: Optional CSS selector for chapter links (e.g. 'ul.chapter-list a').
        session: Optional requests session.
        auto_sort: Whether to auto-sort chapters by number.
        url_pattern: Optional regex pattern to filter chapter URLs.

    Returns:
        List of dicts: [{"title": str, "url": str}, ...]
    """
    logger.info("Parsing catalog: %s", url)
    html, _, _ = utils.download_page(url, session=session)
    soup = BeautifulSoup(html, "lxml")

    chapters = []
    seen_urls = set()
    url_regex = re.compile(url_pattern) if url_pattern else None

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

        # Apply URL pattern filter if provided
        if url_regex and not url_regex.search(clean_url):
            continue

        # Default heuristic filters (only when no selector/pattern explicitly given)
        if not selector and not url_pattern:
            # Skip links to external domains
            if not utils.is_same_domain(clean_url, url):
                continue
            # Skip navigation links with common site page titles
            if NAV_TITLES.match(title):
                continue
            # Skip links with very short URL paths (like "/" or "/about.html")
            path = urlparse(clean_url).path.rstrip("/")
            if len(path) < 5:
                continue
            # Skip category/list/tag/search pages (not chapter links)
            if re.search(r"/(list|category|tag|author|search)/", path, re.I):
                continue
            # Skip links that only have a single numeric segment (book pages, not chapters)
            # Chapter URLs typically have multiple numeric segments like /books/126972/12345.html
            numeric_segments = [s for s in path.split("/") if s.rstrip(".html").isdigit()]
            if numeric_segments and len(numeric_segments) < 2:
                continue
            # Skip shallow paths (e.g. /about.html, /copyright.html) — real chapters
            # typically have deeper paths like /books/126972/25566759.html
            path_segments = [s for s in path.split("/") if s]
            if len(path_segments) < 2:
                continue

        chapters.append({"title": title, "url": clean_url})
        seen_urls.add(clean_url)

    if auto_sort:
        chapters = sort_chapters(chapters)

    logger.info("Found %d chapters", len(chapters))
    return chapters
