import re
import uuid
import logging
import time
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def create_session(retries=3, backoff_factor=0.5):
    """Create a requests session with retry strategy."""
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def download_page(url, session=None, timeout=None):
    """Download a page and return its text content with detected encoding."""
    close_session = False
    if session is None:
        session = create_session()
        close_session = True
    try:
        timeout = timeout or DEFAULT_TIMEOUT
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()

        # Try charset from Content-Type header first
        encoding = None
        content_type = resp.headers.get("Content-Type", "")
        charset_match = re.search(r"charset\s*=\s*([\w-]+)", content_type, re.IGNORECASE)
        if charset_match:
            encoding = charset_match.group(1)

        if encoding:
            resp.encoding = encoding
        else:
            # Try chardet if no charset in header
            import chardet as chardet_mod
            raw = resp.content[:10000]
            result = chardet_mod.detect(raw)
            if result.get("encoding"):
                resp.encoding = result["encoding"]
            else:
                resp.encoding = "utf-8"

        return resp.text, resp.content, resp.encoding
    finally:
        if close_session:
            session.close()


def safe_filename(name, max_length=100):
    """Sanitize a string for use as a filename."""
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    name = re.sub(r"\s+", "_", name)
    return name[:max_length].strip("_. ")


def is_same_domain(url, base_url):
    """Check if url is on the same domain as base_url."""
    base_domain = urlparse(base_url).netloc
    url_domain = urlparse(url).netloc
    if not url_domain:
        return True  # relative path
    return base_domain == url_domain


def generate_uuid():
    return str(uuid.uuid4())
