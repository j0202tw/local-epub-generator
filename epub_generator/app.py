import logging
import os
import threading
import json
import uuid
import shutil
import tempfile
from datetime import datetime

from flask import Flask, render_template, request, jsonify, send_file

from core import utils
from core.catalog_parser import parse_catalog
from core.chapter_fetcher import fetch_chapter
from core.epub_builder import build_epub

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.urandom(24).hex()

# Task management
TASKS = {}
TASKS_LOCK = threading.Lock()
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
TASKS_FILE = os.path.join(DOWNLOAD_DIR, "tasks.json")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def cleanup_old_temp(max_age_hours=1):
    """Clean up temp directories older than max_age_hours."""
    now = datetime.now().timestamp()
    for name in os.listdir(DOWNLOAD_DIR):
        path = os.path.join(DOWNLOAD_DIR, name)
        if os.path.isdir(path) and name.startswith("epub_"):
            age_hours = (now - os.path.getmtime(path)) / 3600
            if age_hours > max_age_hours:
                shutil.rmtree(path, ignore_errors=True)
    # Also prune stale entries from persisted tasks
    _prune_stale_tasks()


def _save_tasks():
    """Persist completed task metadata to disk so downloads survive restarts."""
    with TASKS_LOCK:
        serializable = {}
        for tid, t in TASKS.items():
            if t["status"] in ("completed", "error"):
                entry = {
                    "id": t["id"],
                    "status": t["status"],
                    "title": t["title"],
                    "author": t["author"],
                    "progress": t["progress"],
                    "output_path": t.get("output_path"),
                    "error": t.get("error"),
                }
                serializable[tid] = entry
    try:
        with open(TASKS_FILE, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False)
    except Exception as e:
        logger.warning("Failed to save tasks: %s", e)


def _load_tasks():
    """Load persisted tasks from disk on startup."""
    if not os.path.exists(TASKS_FILE):
        return
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        with TASKS_LOCK:
            for tid, entry in loaded.items():
                if tid not in TASKS and os.path.exists(entry.get("output_path", "")):
                    entry["dir"] = os.path.dirname(entry["output_path"])
                    TASKS[tid] = entry
    except Exception as e:
        logger.warning("Failed to load tasks: %s", e)


def _prune_stale_tasks():
    """Remove persisted tasks whose output files no longer exist."""
    if not os.path.exists(TASKS_FILE):
        return
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        stale = [tid for tid, entry in loaded.items()
                 if not os.path.exists(entry.get("output_path", ""))]
        if stale:
            for tid in stale:
                loaded.pop(tid, None)
            with open(TASKS_FILE, "w", encoding="utf-8") as f:
                json.dump(loaded, f, ensure_ascii=False)
    except Exception as e:
        logger.warning("Failed to prune stale tasks: %s", e)


# ─── Probe / Detection Tool ─────────────────────────────────────────


@app.route("/probe", methods=["POST"])
def probe():
    """Probe a URL to detect content and help user find the right selector."""
    data = request.get_json()
    url = data.get("url", "").strip()
    selector = data.get("selector", "").strip() or None

    if not url:
        return jsonify({"error": "請輸入網址"}), 400
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "請輸入有效的 HTTP/HTTPS 網址"}), 400

    try:
        html, raw_content, encoding = utils.download_page(url)
    except Exception as e:
        return jsonify({"error": f"無法下載頁面: {str(e)}"}), 400

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")

    result = {
        "url": url,
        "encoding": encoding or "unknown",
        "title": str(soup.title.string) if soup.title else "",
        "page_size": len(html),
        "selectors": {},
    }

    # Try user-specified selector
    if selector:
        try:
            selected = soup.select_one(selector)
            if selected:
                text = selected.get_text(strip=True)
                html_preview = str(selected)[:2000]
                result["selectors"][selector] = {
                    "found": True,
                    "text_length": len(text),
                    "text_preview": text[:500],
                    "html_preview": html_preview,
                }
            else:
                result["selectors"][selector] = {"found": False, "error": "選擇器未匹配任何元素"}
        except Exception as e:
            result["selectors"][selector] = {"found": False, "error": str(e)}

    # Auto-detect common selectors
    common_selectors = [
        "article",
        "#content",
        ".content",
        ".post-content",
        ".article-content",
        ".chapter-content",
        ".entry-content",
        "#chaptercontent",
        ".chapter",
        ".read-content",
        ".story-content",
        ".text-content",
        "main",
        "#main-content",
        ".main-body",
    ]

    for cs in common_selectors:
        try:
            el = soup.select_one(cs)
            if el:
                text_len = len(el.get_text(strip=True))
                if text_len > 100:
                    result["selectors"][cs] = {
                        "found": True,
                        "text_length": text_len,
                        "text_preview": el.get_text(strip=True)[:300],
                    }
        except Exception:
            pass

    # Page structure info (tag hierarchy for debugging)
    structure = []
    for tag in soup.find_all(["div", "section", "article", "main"], limit=30):
        classes = " ".join(tag.get("class", []))
        tag_id = tag.get("id", "")
        text_len = len(tag.get_text(strip=True))
        if text_len > 50:
            label = tag.name
            if tag_id:
                label += f"#{tag_id}"
            elif classes:
                label += f".{classes.replace(' ', '.')}"
            structure.append({
                "tag": label,
                "text_length": text_len,
                "text_preview": tag.get_text(strip=True)[:200],
            })

    result["structure"] = structure[:15]  # top 15 content blocks
    return jsonify(result)


# ─── EPUB Generation ────────────────────────────────────────────────


@app.route("/generate", methods=["POST"])
def generate():
    """Start an EPUB generation task."""
    data = request.get_json()
    title = data.get("title", "").strip()
    author = data.get("author", "").strip()
    catalog_url = data.get("catalog_url", "").strip()
    catalog_selector = data.get("catalog_selector", "").strip() or None
    content_selector = data.get("content_selector", "").strip() or None
    url_pattern = data.get("url_pattern", "").strip() or None
    max_threads = int(data.get("max_threads", 5))

    if not all([title, author, catalog_url]):
        return jsonify({"error": "請填寫書名、作者和目錄網址"}), 400
    if not catalog_url.startswith(("http://", "https://")):
        return jsonify({"error": "請輸入有效的 HTTP/HTTPS 目錄網址"}), 400

    task_id = uuid.uuid4().hex[:12]
    task_dir = tempfile.mkdtemp(prefix=f"epub_{task_id}_", dir=DOWNLOAD_DIR)

    task = {
        "id": task_id,
        "status": "pending",
        "title": title,
        "author": author,
        "progress": {"total": 0, "completed": 0, "failed": 0, "failed_list": []},
        "output_path": None,
        "error": None,
        "dir": task_dir,
    }

    with TASKS_LOCK:
        TASKS[task_id] = task

    thread = threading.Thread(
        target=_run_generation,
        args=(task_id, title, author, catalog_url, catalog_selector, content_selector, url_pattern, max_threads, task_dir),
        daemon=True,
    )
    thread.start()

    return jsonify({"task_id": task_id})


def _run_generation(task_id, title, author, catalog_url, catalog_selector, content_selector, url_pattern, max_threads, task_dir):
    """Background task: parse catalog, fetch chapters, build EPUB."""
    task = TASKS.get(task_id)
    if not task:
        return

    try:
        # Step 1: Parse catalog
        logger.info("Task %s: parsing catalog...", task_id)
        task["status"] = "parsing_catalog"
        chapters = parse_catalog(catalog_url, selector=catalog_selector, url_pattern=url_pattern)

        if not chapters:
            task["status"] = "error"
            task["error"] = "無法從目錄頁解析到任何章節"
            _save_tasks()
            return

        total = len(chapters)
        task["progress"]["total"] = total
        task["status"] = "fetching"

        # Step 2: Fetch chapters
        fetched_chapters = []  # (url, result) tuples
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def fetch_one(ch):
            try:
                result = fetch_chapter(
                    ch["url"],
                    selector=content_selector,
                    fallback_title=ch["title"],
                    image_dir=task_dir,
                )
                return (ch["url"], result, None)
            except Exception as e:
                return (ch["url"], None, str(e))

        with ThreadPoolExecutor(max_workers=max_threads) as executor:
            futures = {executor.submit(fetch_one, ch): ch for ch in chapters}
            for future in as_completed(futures):
                ch = futures[future]
                url, result, error = future.result()
                with TASKS_LOCK:
                    t = TASKS.get(task_id)
                    if not t:
                        return
                    if error:
                        t["progress"]["failed"] += 1
                        t["progress"]["failed_list"].append(ch["title"])
                        logger.warning("Task %s: failed to fetch '%s': %s", task_id, ch["title"], error)
                    else:
                        fetched_chapters.append((url, result))
                        t["progress"]["completed"] += 1

        if not fetched_chapters:
            task["status"] = "error"
            task["error"] = "所有章節抓取失敗"
            _save_tasks()
            return

        # Step 3: Sort by catalog order (catalog is already sorted by chapter number)
        url_order = {ch["url"]: i for i, ch in enumerate(chapters)}
        fetched_chapters.sort(key=lambda x: url_order.get(x[0], 999))
        sorted_chapters = [result for _, result in fetched_chapters]

        # Step 4: Build EPUB
        logger.info("Task %s: building EPUB...", task["id"])
        task["status"] = "building"

        safe_title = utils.safe_filename(title)
        safe_author = utils.safe_filename(author)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        epub_filename = f"{safe_title}_{safe_author}_{timestamp}.epub"
        epub_path = os.path.join(task_dir, epub_filename)

        build_epub(
            title=title,
            author=author,
            chapters=sorted_chapters,
            output_path=epub_path,
        )

        task["status"] = "completed"
        task["output_path"] = epub_path
        _save_tasks()

    except Exception as e:
        logger.exception("Task %s failed: %s", task_id, e)
        task["status"] = "error"
        task["error"] = str(e)
        _save_tasks()


# ─── Task Status / Download ─────────────────────────────────────────


@app.route("/status/<task_id>")
def task_status(task_id):
    with TASKS_LOCK:
        task = TASKS.get(task_id)
    if not task:
        return jsonify({"error": "任務不存在"}), 404
    return jsonify({
        "status": task["status"],
        "progress": task["progress"],
        "error": task.get("error"),
    })


@app.route("/download/<task_id>")
def task_download(task_id):
    with TASKS_LOCK:
        task = TASKS.get(task_id)
    if not task or task["status"] != "completed" or not task.get("output_path"):
        return jsonify({"error": "檔案尚未就緒或不存在"}), 404

    output_path = task["output_path"]
    if not os.path.exists(output_path):
        return jsonify({"error": "檔案不存在"}), 404

    return send_file(
        output_path,
        mimetype="application/epub+zip",
        as_attachment=True,
        download_name=os.path.basename(output_path),
    )


# ─── Main Page ──────────────────────────────────────────────────────


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/catalog-preview", methods=["POST"])
def catalog_preview():
    """Preview catalog links from a URL."""
    data = request.get_json()
    url = data.get("url", "").strip()
    selector = data.get("selector", "").strip() or None
    url_pattern = data.get("url_pattern", "").strip() or None

    if not url:
        return jsonify({"error": "請輸入目錄網址"}), 400

    try:
        chapters = parse_catalog(url, selector=selector, url_pattern=url_pattern)
        return jsonify({
            "total": len(chapters),
            "chapters": chapters[:100],  # limit preview
        })
    except Exception as e:
        return jsonify({"error": f"解析目錄失敗: {str(e)}"}), 400


if __name__ == "__main__":
    cleanup_old_temp()
    _load_tasks()
    logger.info("Starting EPUB Generator on http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=True, threaded=True)
