"""
05a_dedup.py — Deduplicate extracted books in PKM-Index/chunks/.

Groups books by normalised title. When duplicates exist, keeps the copy
with the most chapters (richest extraction) and moves extras to
PKM-Index/chunks_dupes/. Nothing is deleted.
"""

import json
import logging
import re
import shutil
import unicodedata
from collections import defaultdict
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────
INDEX_DIR = Path(r"N:\Projects\PKM\PKM-Index")
CHUNKS_DIR = INDEX_DIR / "chunks"
DUPES_DIR = INDEX_DIR / "chunks_dupes"
LOG_DIR = INDEX_DIR / "logs"


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "dedup.log"

    logger = logging.getLogger("dedup")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s"))
        logger.addHandler(fh)
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(levelname)-7s  %(message)s"))
        logger.addHandler(ch)

    return logger


def normalise_title(title: str) -> str:
    """Normalise a title for dedup comparison."""
    title = unicodedata.normalize("NFKD", title)
    title = title.lower().strip()
    # Remove common noise: edition markers, ISBN, file extensions
    title = re.sub(r"\b(isbn|pdfdrive|ebook|pdf|epub|mobi)\b", "", title)
    title = re.sub(r"\b\d{10,13}\b", "", title)  # ISBNs
    title = re.sub(r"\(.*?\)", "", title)          # Parenthetical info
    title = re.sub(r"[^\w\s]", "", title)          # Punctuation
    title = re.sub(r"\s+", " ", title).strip()
    return title


def load_metadata(book_dir: Path) -> dict:
    meta_file = book_dir / "metadata.json"
    if meta_file.exists():
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"title": book_dir.name, "chapter_count": 0}


def count_chapters(book_dir: Path) -> int:
    """Count .txt chapter files in a book directory."""
    return len(list(book_dir.glob("*.txt")))


def main():
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("Starting deduplication")

    if not CHUNKS_DIR.exists():
        logger.error("Chunks directory not found: %s", CHUNKS_DIR)
        return

    DUPES_DIR.mkdir(parents=True, exist_ok=True)

    # ── Group books by normalised title ────────────────────────────────
    book_dirs = sorted([d for d in CHUNKS_DIR.iterdir() if d.is_dir()])
    logger.info("Found %d book folders", len(book_dirs))

    groups = defaultdict(list)
    for book_dir in book_dirs:
        meta = load_metadata(book_dir)
        title = meta.get("title", book_dir.name)
        norm = normalise_title(title)

        # Skip books with empty/useless normalised titles
        if len(norm) < 3:
            continue

        n_chapters = count_chapters(book_dir)
        groups[norm].append({
            "dir": book_dir,
            "title": title,
            "chapters": n_chapters,
        })

    # ── Process duplicate groups ───────────────────────────────────────
    dupe_groups = {k: v for k, v in groups.items() if len(v) > 1}
    logger.info("Found %d duplicate groups (of %d total normalised titles)",
                len(dupe_groups), len(groups))

    moved = 0
    kept = 0

    for norm_title, entries in sorted(dupe_groups.items()):
        # Sort by chapter count descending — keep the richest
        entries.sort(key=lambda e: e["chapters"], reverse=True)

        best = entries[0]
        dupes = entries[1:]

        logger.info("GROUP: '%s' — keeping '%s' (%d chapters), "
                     "moving %d duplicates",
                     norm_title[:60], best["dir"].name, best["chapters"],
                     len(dupes))
        kept += 1

        for dupe in dupes:
            src = dupe["dir"]
            dst = DUPES_DIR / src.name
            # Handle name collision in dupes folder
            if dst.exists():
                dst = DUPES_DIR / f"{src.name}__dup{moved}"

            try:
                shutil.move(str(src), str(dst))
                logger.info("  MOVED: %s (%d chapters) → chunks_dupes/",
                            src.name, dupe["chapters"])
                moved += 1
            except Exception as e:
                logger.error("  FAIL moving %s: %s", src.name, e)

    # ── Summary ────────────────────────────────────────────────────────
    remaining = len(list(CHUNKS_DIR.iterdir()))
    logger.info("-" * 60)
    logger.info("Done. Duplicate groups: %d | Moved to dupes: %d | "
                "Remaining in chunks: %d",
                len(dupe_groups), moved, remaining)
    print(f"\nDone! Duplicate groups: {len(dupe_groups)} | "
          f"Moved: {moved} | Remaining: {remaining}")


if __name__ == "__main__":
    main()
