import logging
import os
import datetime

from ebooklib import epub

from . import utils

logger = logging.getLogger(__name__)

DEFAULT_CSS = """
body {
    font-family: "Noto Serif", "Source Han Serif", serif;
    line-height: 1.8;
    margin: 1em 2em;
    font-size: 1em;
}
h1 { font-size: 1.6em; text-align: center; margin: 1em 0; }
h2 { font-size: 1.3em; margin: 0.8em 0; }
h3 { font-size: 1.15em; margin: 0.6em 0; }
p { text-indent: 2em; margin: 0.5em 0; }
strong, b { font-weight: bold; }
em, i { font-style: italic; }
img { max-width: 100%; height: auto; display: block; margin: 1em auto; }
blockquote {
    border-left: 3px solid #ccc;
    margin: 1em 0;
    padding: 0.5em 1em;
    color: #555;
}
pre, code { font-family: "Consolas", "Monaco", monospace; }
pre {
    background: #f5f5f5;
    padding: 1em;
    overflow-x: auto;
    border-radius: 4px;
}
table { border-collapse: collapse; width: 100%; margin: 1em 0; }
td, th { border: 1px solid #ddd; padding: 0.5em; }
"""


def build_epub(
    title,
    author,
    chapters,
    output_path,
    lang="zh-CN",
    cover_image=None,
):
    """Build an EPUB 3 file from chapter data.

    Args:
        title: Book title.
        author: Book author.
        chapters: List of dicts with keys: title, body_html, images.
        output_path: Where to save the .epub file.
        lang: Language code.
        cover_image: Optional path to cover image.

    Returns:
        Path to the generated .epub file.
    """
    book = epub.EpubBook()

    # Metadata
    book.set_identifier(utils.generate_uuid())
    book.set_title(title)
    book.set_language(lang)
    book.add_author(author)

    # CSS
    css_item = epub.EpubItem(
        uid="style",
        file_name="style/default.css",
        media_type="text/css",
        content=DEFAULT_CSS.encode("utf-8"),
    )
    book.add_item(css_item)

    # Cover
    if cover_image and os.path.exists(cover_image):
        try:
            with open(cover_image, "rb") as f:
                cover_content = f.read()
            cover_item = epub.EpubImage(
                uid="cover",
                file_name="images/cover.jpg",
                media_type="image/jpeg",
                content=cover_content,
            )
            book.add_item(cover_item)
            book.set_cover("images/cover.jpg", cover_content)
        except Exception as e:
            logger.warning("Failed to add cover: %s", e)

    # Chapters
    epub_chapters = []
    image_map = {}  # file_name -> image_item

    # First pass: collect all images
    for i, ch in enumerate(chapters):
        for img in ch.get("images", []):
            if img["file_name"] not in image_map:
                try:
                    with open(img["local_path"], "rb") as f:
                        img_data = f.read()
                    img_item = epub.EpubImage(
                        uid=f"img_{len(image_map)}",
                        file_name=f"images/{img['file_name']}",
                        media_type=img.get("media_type", "image/jpeg"),
                        content=img_data,
                    )
                    book.add_item(img_item)
                    image_map[img["file_name"]] = img_item
                except Exception as e:
                    logger.warning("Failed to add image %s: %s", img["file_name"], e)

    # Second pass: create chapter items
    for i, ch in enumerate(chapters):
        epub_ch = epub.EpubHtml(
            title=ch["title"],
            file_name=f"chapter_{i+1}.xhtml",
            lang=lang,
        )
        content = f"<h1>{ch['title']}</h1>\n{ch['body_html']}"
        epub_ch.content = content
        epub_ch.add_item(css_item)
        book.add_item(epub_ch)
        epub_chapters.append(epub_ch)

    # TOC (Table of Contents)
    book.toc = epub_chapters

    # Add navigation files
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # Define spine (reading order)
    spine_items = epub_chapters
    book.spine = spine_items

    # Write
    epub.write_epub(output_path, book)
    logger.info("EPUB saved to: %s", output_path)
    return output_path
