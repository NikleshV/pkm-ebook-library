"""
02_extract_text.py — Extract text from .pdf, .epub, .docx files in the library.

Splits text into chapters/sections and saves as individual .txt files under
PKM-Index/chunks/<sanitised_book_title>/. Also saves metadata.json per book.
Idempotent: skips books whose chunk folder already exists.
"""

import json
import logging
import re
import unicodedata
from pathlib import Path

import fitz  # PyMuPDF
import ebooklib
from ebooklib import epub
from docx import Document
from bs4 import BeautifulSoup
from tqdm import tqdm

# ── Configuration ──────────────────────────────────────────────────────────────
LIBRARY_DIR = Path(r"C:\Users\Niklesh\OneDrive\Documents\Library")
INDEX_DIR = Path(r"N:\Projects\PKM\PKM-Index")
CHUNKS_DIR = INDEX_DIR / "chunks"
LOG_DIR = INDEX_DIR / "logs"

SUPPORTED_EXTENSIONS = {".pdf", ".epub", ".docx"}
SKIP_DIRS = {".caltrash"}
PDF_PAGES_PER_CHUNK = 20
MAX_TITLE_LENGTH = 80

# Characters outside Basic Latin + Latin Extended are considered non-Latin.
# Used to detect non-English filenames and text content.
NON_LATIN_THRESHOLD_FILENAME = 0.3   # 30% non-Latin chars in filename → skip
NON_LATIN_THRESHOLD_TEXT = 0.25      # 25% non-Latin chars in extracted text → skip


# ── Helpers ────────────────────────────────────────────────────────────────────

def _non_latin_ratio(text: str) -> float:
    """Return the fraction of alphabetic characters that are outside Latin scripts."""
    alpha_chars = [ch for ch in text if ch.isalpha()]
    if not alpha_chars:
        return 0.0
    non_latin = sum(
        1 for ch in alpha_chars
        if unicodedata.category(ch).startswith("L")
        and not ("\u0000" <= ch <= "\u024F")  # Basic Latin + Latin Extended A/B
    )
    return non_latin / len(alpha_chars)


def is_non_english_filename(filepath: Path) -> bool:
    """Check if the filename suggests non-English content."""
    name = filepath.stem
    return _non_latin_ratio(name) > NON_LATIN_THRESHOLD_FILENAME


def is_non_english_text(text: str, sample_size: int = 2000) -> bool:
    """Check if extracted text is predominantly non-English (non-Latin script)."""
    # Sample from the middle of the text for a better signal
    if len(text) > sample_size * 2:
        mid = len(text) // 2
        sample = text[mid - sample_size : mid + sample_size]
    else:
        sample = text
    return _non_latin_ratio(sample) > NON_LATIN_THRESHOLD_TEXT


def sanitise_title(name: str) -> str:
    """Convert a filename/title into a safe folder name."""
    # Remove file extension if present
    name = Path(name).stem
    # Normalize unicode
    name = unicodedata.normalize("NFKD", name)
    # Replace non-alphanumeric (keeping spaces and hyphens) with underscores
    name = re.sub(r"[^\w\s-]", "_", name)
    # Collapse whitespace/underscores
    name = re.sub(r"[\s_]+", "_", name).strip("_")
    # Truncate
    return name[:MAX_TITLE_LENGTH]


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "extract.log"

    logger = logging.getLogger("extract")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s"))
        logger.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(levelname)-7s  %(message)s"))
        logger.addHandler(ch)

    return logger


def find_books(root: Path, logger: logging.Logger = None):
    """Yield all supported book files, skipping SKIP_DIRS and non-English filenames."""
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if is_non_english_filename(path):
            if logger:
                logger.info("SKIP (non-English filename): %s", path.name)
            continue
        yield path


# ── Extractors ─────────────────────────────────────────────────────────────────

def extract_epub(filepath: Path) -> list[dict]:
    """Extract text from EPUB by spine item (chapter). Returns list of {title, text}."""
    chapters = []
    try:
        book = epub.read_epub(str(filepath), options={"ignore_ncx": True})
        spine_ids = [item_id for item_id, _ in book.spine]
        items_by_id = {item.get_id(): item for item in book.get_items()}

        chapter_num = 0
        for item_id in spine_ids:
            item = items_by_id.get(item_id)
            if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue
            html_content = item.get_content().decode("utf-8", errors="replace")
            soup = BeautifulSoup(html_content, "html.parser")
            text = soup.get_text(separator="\n", strip=True)
            # Skip very short sections (TOC pages, copyright, etc.)
            if len(text.strip()) < 100:
                continue
            chapter_num += 1
            chapters.append({
                "title": f"chapter_{chapter_num:03d}",
                "text": text,
            })
    except Exception as e:
        raise RuntimeError(f"EPUB extraction failed: {e}") from e
    return chapters


def extract_pdf(filepath: Path) -> list[dict]:
    """Extract text from PDF, grouped into chunks of ~PDF_PAGES_PER_CHUNK pages."""
    chapters = []
    try:
        doc = fitz.open(str(filepath))
        total_pages = len(doc)
        chunk_num = 0

        for start in range(0, total_pages, PDF_PAGES_PER_CHUNK):
            end = min(start + PDF_PAGES_PER_CHUNK, total_pages)
            text_parts = []
            for page_num in range(start, end):
                page = doc[page_num]
                text_parts.append(page.get_text())
            text = "\n".join(text_parts)
            if len(text.strip()) < 50:
                continue
            chunk_num += 1
            chapters.append({
                "title": f"section_{chunk_num:03d}_pages_{start+1}_to_{end}",
                "text": text,
            })
        doc.close()
    except Exception as e:
        raise RuntimeError(f"PDF extraction failed: {e}") from e
    return chapters


def extract_docx(filepath: Path) -> list[dict]:
    """Extract text from DOCX, splitting on Heading 1/2 styles."""
    chapters = []
    try:
        doc = Document(str(filepath))
        current_title = "section_001"
        current_text = []
        chapter_num = 0

        for para in doc.paragraphs:
            style = para.style.name if para.style else ""
            if style in ("Heading 1", "Heading 2") and current_text:
                # Save previous chapter
                text = "\n".join(current_text)
                if len(text.strip()) >= 50:
                    chapter_num += 1
                    chapters.append({
                        "title": f"chapter_{chapter_num:03d}",
                        "text": text,
                    })
                current_text = []
                current_title = para.text.strip() or f"chapter_{chapter_num + 1:03d}"

            current_text.append(para.text)

        # Save last section
        if current_text:
            text = "\n".join(current_text)
            if len(text.strip()) >= 50:
                chapter_num += 1
                chapters.append({
                    "title": f"chapter_{chapter_num:03d}",
                    "text": text,
                })
    except Exception as e:
        raise RuntimeError(f"DOCX extraction failed: {e}") from e
    return chapters


EXTRACTORS = {
    ".epub": extract_epub,
    ".pdf": extract_pdf,
    ".docx": extract_docx,
}


# ── Metadata ───────────────────────────────────────────────────────────────────

def get_metadata(filepath: Path, chapter_count: int) -> dict:
    """Build a metadata dict for a book file."""
    title = filepath.stem
    author = "Unknown"

    # Try to get author from PDF metadata
    if filepath.suffix.lower() == ".pdf":
        try:
            doc = fitz.open(str(filepath))
            meta = doc.metadata
            if meta.get("author"):
                author = meta["author"]
            if meta.get("title"):
                title = meta["title"]
            doc.close()
        except Exception:
            pass

    # Try to get author from EPUB metadata
    elif filepath.suffix.lower() == ".epub":
        try:
            book = epub.read_epub(str(filepath), options={"ignore_ncx": True})
            creators = book.get_metadata("DC", "creator")
            if creators:
                author = creators[0][0]
            titles = book.get_metadata("DC", "title")
            if titles:
                title = titles[0][0]
        except Exception:
            pass

    file_size_mb = round(filepath.stat().st_size / (1024 * 1024), 2)

    return {
        "title": title,
        "author": author,
        "format": filepath.suffix.lower().lstrip("."),
        "source_path": str(filepath),
        "chapter_count": chapter_count,
        "year": "Unknown",
        "file_size_mb": file_size_mb,
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def process_book(filepath: Path, logger: logging.Logger) -> str:
    """Process a single book. Returns 'success', 'skip', or 'fail'."""
    safe_name = sanitise_title(filepath.stem)
    book_dir = CHUNKS_DIR / safe_name

    # Idempotent: skip if already processed
    if book_dir.exists():
        return "skip"

    ext = filepath.suffix.lower()
    extractor = EXTRACTORS.get(ext)
    if not extractor:
        logger.warning("No extractor for %s: %s", ext, filepath)
        return "fail"

    try:
        chapters = extractor(filepath)
    except Exception as e:
        logger.error("FAIL: %s — %s", filepath.name, e)
        return "fail"

    if not chapters:
        logger.warning("EMPTY (no text extracted): %s", filepath.name)
        return "fail"

    # Check if extracted text is non-English
    combined_sample = " ".join(ch["text"] for ch in chapters[:3])  # sample first 3 chapters
    if is_non_english_text(combined_sample):
        logger.info("SKIP (non-English text content): %s", filepath.name)
        return "skip_lang"

    # Save chapters
    book_dir.mkdir(parents=True, exist_ok=True)
    for ch in chapters:
        txt_file = book_dir / f"{ch['title']}.txt"
        txt_file.write_text(ch["text"], encoding="utf-8")

    # Save metadata
    metadata = get_metadata(filepath, len(chapters))
    meta_file = book_dir / "metadata.json"
    meta_file.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("OK: %s → %d chapters", filepath.name, len(chapters))
    return "success"


def main():
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("Starting text extraction")
    logger.info("Library: %s", LIBRARY_DIR)
    logger.info("Output:  %s", CHUNKS_DIR)

    if not LIBRARY_DIR.exists():
        logger.error("Library directory not found: %s", LIBRARY_DIR)
        return

    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)

    book_files = list(find_books(LIBRARY_DIR, logger))
    total = len(book_files)
    logger.info("Found %d book files to process (after filename language filter)", total)

    success = 0
    skipped = 0
    skipped_lang = 0
    failed = 0

    for filepath in tqdm(book_files, desc="Extracting", unit="book"):
        result = process_book(filepath, logger)
        if result == "success":
            success += 1
        elif result == "skip":
            skipped += 1
        elif result == "skip_lang":
            skipped_lang += 1
        else:
            failed += 1

    logger.info("-" * 60)
    logger.info("Done. Total: %d | Extracted: %d | Skipped (done): %d | "
                "Skipped (non-English): %d | Failed: %d",
                total, success, skipped, skipped_lang, failed)
    print(f"\nDone! Extracted: {success} | Skipped (done): {skipped} | "
          f"Skipped (non-English): {skipped_lang} | Failed: {failed}")


if __name__ == "__main__":
    main()
