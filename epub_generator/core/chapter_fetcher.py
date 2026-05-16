import logging
import os
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from . import utils

logger = logging.getLogger(__name__)

# HTML tags to preserve during cleaning
KEEP_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "b", "em", "i", "u", "br", "hr",
    "ul", "ol", "li", "blockquote", "pre", "code",
    "img", "a", "span", "div", "table", "tr", "td", "th", "thead", "tbody",
    "sub", "sup", "del", "ins",
}

# Tags to remove entirely
REMOVE_TAGS = {"script", "style", "noscript", "iframe", "nav", "footer", "aside",
               "form", "input", "button", "select", "textarea", "svg", "canvas"}

# Class/id patterns that suggest non-content areas
JUNK_PATTERNS = re.compile(
    r"comment|sidebar|advert|footer|header|nav|menu|toolbar|breadcrumb"
    r"|related|recommend|share|social|top|bottom|banner|copyright", re.I
)


def _clean_title(raw_title, book_title=None):
    """Clean a raw extracted title by stripping site suffixes and book prefixes."""
    if not raw_title:
        return raw_title

    title = raw_title.strip()

    # Split by common separators and look for the chapter-specific part
    separators = [" - ", " — ", " | ", " _ ", " / ", " \\ ", " - ", "–", "—"]
    parts = [title]
    for sep in separators:
        if sep in title:
            parts = [p.strip() for p in title.split(sep)]
            break

    # If we have multiple parts, pick the most relevant one
    if len(parts) > 1:
        # Prefer the part that looks like a chapter title (contains numbers or is medium-length)
        chapter_parts = [p for p in parts if re.search(r"[第章卷\d]", p)]
        if chapter_parts:
            return chapter_parts[0]
        # Otherwise return the first non-short part
        non_short = [p for p in parts if len(p) > 4]
        return non_short[0] if non_short else parts[0]

    return title


def extract_title(soup, fallback_title=None):
    """Extract title from a chapter page.

    Priority: <title> tag > <h1> in content area > fallback_title.
    Skips <h1> elements that look like site logos/headers.
    """
    # Prefer <title> which is usually the most specific
    title_tag = soup.find("title")
    if title_tag:
        title_text = title_tag.get_text(strip=True)
        if title_text and len(title_text) > 4:
            return _clean_title(title_text)

    # Try <h1> but skip if it looks like a site logo (short text)
    h1 = soup.find("h1")
    if h1:
        h1_text = h1.get_text(strip=True)
        if h1_text and len(h1_text) > 4:
            return _clean_title(h1_text)

    return fallback_title or "Untitled"


def extract_body(soup, selector=None):
    """Extract the main content body from a chapter page.

    If a CSS selector is provided, use it. Otherwise use heuristics.
    """
    if selector:
        elem = soup.select_one(selector)
        if elem:
            return elem

    # Heuristic: find the largest content-like element
    candidates = soup.find_all(["article", "div", "section", "main"])
    if not candidates:
        candidates = soup.find_all("div")

    best = None
    best_len = 0

    for el in candidates:
        # Skip if it has junk class/id
        el_class = " ".join(el.get("class", []))
        el_id = el.get("id", "")
        if JUNK_PATTERNS.search(el_class) or JUNK_PATTERNS.search(el_id):
            continue
        text_len = len(el.get_text(strip=True))
        if text_len > best_len:
            best_len = text_len
            best = el

    return best or soup.body or soup


def clean_html(elem):
    """Clean HTML: remove junk tags, keep useful formatting, preserve images."""
    # Remove unwanted tags
    for tag in elem.find_all(REMOVE_TAGS):
        tag.decompose()

    # Remove elements with junk class/id patterns
    for el in elem.find_all(class_=JUNK_PATTERNS):
        el.decompose()
    for el in elem.find_all(id=JUNK_PATTERNS):
        el.decompose()

    # Walk through and keep only allowed tags (but keep text in others)
    for tag in elem.find_all():
        if tag.name not in KEEP_TAGS:
            tag.unwrap()

    # Strip presentation attributes so EPUB reader font/size controls work
    REMOVE_ATTRS = {"style", "class", "id", "width", "height", "align", "valign",
                    "bgcolor", "color", "cellpadding", "cellspacing", "border"}
    for tag in elem.find_all():
        for attr in REMOVE_ATTRS:
            tag.attrs.pop(attr, None)

    return elem


def download_images(elem, page_url, image_dir):
    """Download images referenced in the HTML and update src attributes."""
    session = utils.create_session(retries=2)
    images_meta = []

    for i, img in enumerate(elem.find_all("img")):
        src = img.get("src") or img.get("data-src")
        if not src:
            continue

        full_src = urljoin(page_url, src)
        try:
            resp = session.get(full_src, timeout=10)
            resp.raise_for_status()

            ext = os.path.splitext(full_src.split("?")[0])[1] or ".jpg"
            local_name = f"img_{i}{ext}"
            local_path = os.path.join(image_dir, local_name)
            with open(local_path, "wb") as f:
                f.write(resp.content)

            content_type = resp.headers.get("Content-Type", "image/jpeg")
            img["src"] = f"../images/{local_name}"
            images_meta.append({
                "local_path": local_path,
                "media_type": content_type,
                "file_name": local_name,
            })
        except Exception as e:
            logger.warning("Failed to download image %s: %s", full_src, e)
            # Keep original src as fallback

    return images_meta


def _pick_best_title(extracted_title, fallback_title):
    """Pick the best title between extracted and fallback.

    If the fallback (catalog) title is contained within or is clearly
    part of the extracted title, prefer the cleaner fallback.
    """
    if not fallback_title:
        return extracted_title
    if not extracted_title:
        return fallback_title

    # If fallback is a substring of extracted, use the cleaner fallback
    if fallback_title in extracted_title:
        return fallback_title

    # If extracted is much longer and contains keywords from fallback
    # (e.g. both have "Chapter 1" or "第1章"), prefer the shorter one
    if len(extracted_title) > len(fallback_title) * 2:
        # Check if they share chapter-specific keywords
        chapter_nums = re.findall(r"[第章卷\d]+", extracted_title)
        fallback_nums = re.findall(r"[第章卷\d]+", fallback_title)
        if chapter_nums and fallback_nums and chapter_nums == fallback_nums:
            return fallback_title

    return extracted_title


def fetch_chapter(url, selector=None, fallback_title=None, image_dir=None):
    """Fetch and extract content from a single chapter URL.

    Returns:
        dict with keys: title, body_html, images
    """
    logger.info("Fetching chapter: %s", url)
    html, _, _ = utils.download_page(url)
    soup = BeautifulSoup(html, "lxml")

    title = _pick_best_title(extract_title(soup, fallback_title), fallback_title)
    body = extract_body(soup, selector)
    body = clean_html(body)

    images_meta = []
    if image_dir and body.find("img"):
        images_meta = download_images(body, url, image_dir)

    return {
        "title": title,
        "body_html": str(body),
        "images": images_meta,
    }
