"""
01_convert_mobi.py — Convert .mobi files to .epub using Calibre's ebook-convert CLI.

Scans the library recursively, skips .caltrash and files that already have a
matching .epub. Logs results to PKM-Index/logs/convert.log.
"""

import subprocess
import logging
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────
LIBRARY_DIR = Path(r"C:\Users\Niklesh\OneDrive\Documents\Library")
INDEX_DIR = Path(r"N:\Projects\PKM\PKM-Index")
LOG_DIR = INDEX_DIR / "logs"
CALIBRE_CONVERT = Path(r"C:\Program Files\Calibre2\ebook-convert.exe")

SKIP_DIRS = {".caltrash"}


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "convert.log"

    logger = logging.getLogger("convert")
    logger.setLevel(logging.INFO)

    # File handler
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s"))
    logger.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(levelname)-7s  %(message)s"))
    logger.addHandler(ch)

    return logger


def find_mobi_files(root: Path):
    """Yield all .mobi files under root, skipping SKIP_DIRS."""
    for path in root.rglob("*.mobi"):
        # Skip if any parent directory is in SKIP_DIRS
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def convert_mobi_to_epub(mobi_path: Path, logger: logging.Logger) -> bool:
    """Convert a single .mobi to .epub. Returns True on success."""
    epub_path = mobi_path.with_suffix(".epub")

    # Skip if .epub already exists
    if epub_path.exists():
        logger.info("SKIP (epub exists): %s", mobi_path)
        return True

    # Check that Calibre is available
    if not CALIBRE_CONVERT.exists():
        logger.error("Calibre ebook-convert not found at: %s", CALIBRE_CONVERT)
        return False

    try:
        result = subprocess.run(
            [str(CALIBRE_CONVERT), str(mobi_path), str(epub_path)],
            capture_output=True,
            text=True,
            timeout=300,  # 5 min timeout per file
        )
        if result.returncode == 0:
            logger.info("OK: %s -> %s", mobi_path.name, epub_path.name)
            return True
        else:
            logger.error("FAIL (returncode %d): %s\n  stderr: %s",
                         result.returncode, mobi_path, result.stderr[:500])
            return False
    except subprocess.TimeoutExpired:
        logger.error("FAIL (timeout): %s", mobi_path)
        return False
    except Exception as e:
        logger.error("FAIL (exception): %s — %s", mobi_path, e)
        return False


def main():
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("Starting .mobi → .epub conversion")
    logger.info("Library: %s", LIBRARY_DIR)

    if not LIBRARY_DIR.exists():
        logger.error("Library directory not found: %s", LIBRARY_DIR)
        return

    mobi_files = list(find_mobi_files(LIBRARY_DIR))
    total = len(mobi_files)
    logger.info("Found %d .mobi files to process", total)

    success = 0
    skipped = 0
    failed = 0

    for i, mobi_path in enumerate(mobi_files, 1):
        print(f"[{i}/{total}] {mobi_path.name}")

        epub_path = mobi_path.with_suffix(".epub")
        if epub_path.exists():
            skipped += 1
            logger.info("SKIP (epub exists): %s", mobi_path)
            continue

        if convert_mobi_to_epub(mobi_path, logger):
            success += 1
        else:
            failed += 1

    logger.info("-" * 60)
    logger.info("Done. Total: %d | Converted: %d | Skipped: %d | Failed: %d",
                total, success, skipped, failed)
    print(f"\nDone! Converted: {success} | Skipped: {skipped} | Failed: {failed}")


if __name__ == "__main__":
    main()
