"""
05_generate_notes.py — Generate structured Obsidian notes for each book via Claude API.

Reads extracted chapter text from PKM-Index/chunks/, sends to Claude
(claude-sonnet-4-20250514) with a knowledge-extraction prompt, and saves the
resulting markdown note to the Obsidian vault. Idempotent: skips books
whose note already exists. Requires ANTHROPIC_API_KEY environment variable.
"""

import json
import logging
import os
import re
import time
import unicodedata
from pathlib import Path

import anthropic

# ── Configuration ──────────────────────────────────────────────────────────────
INDEX_DIR = Path(r"N:\Projects\PKM\PKM-Index")
CHUNKS_DIR = INDEX_DIR / "chunks"
LOG_DIR = INDEX_DIR / "logs"

OBSIDIAN_VAULT = Path(
    r"C:\Users\Niklesh\OneDrive\Documents\MD Obsidian\Niklesh Notes_Main"
)
NOTES_DIR = OBSIDIAN_VAULT / "PKM-Library" / "Books"

MODEL = "claude-sonnet-4-20250514"
MAX_WORDS = 6000
API_DELAY_SECONDS = 3
MAX_TITLE_LENGTH = 80

SYSTEM_PROMPT = (
    "You are a knowledge extraction assistant. Given the text of a book, "
    "generate a structured Obsidian markdown note. Output ONLY valid markdown "
    "with YAML frontmatter. No preamble."
)

NOTE_TEMPLATE_INSTRUCTIONS = """\
Generate an Obsidian markdown note with this exact structure:

---
title: "{title}"
author: "{author}"
year: {year}
tags: [tag1, tag2, tag3]  # 3-5 topic tags, lowercase, no hash symbols
format: {format}
read_status: "unread"
anki_exported: false
---

# {title}

## 30-Second Summary
(2-3 sentences capturing the core thesis)

## Key Concepts
(Bullet list of 5-10 key concepts. Use [[wikilinks]] for important concepts
that could link to other notes, e.g. [[Systems Thinking]], [[Lean Manufacturing]])

## Key Arguments or Frameworks
(Bullet list of the main arguments, models, or frameworks presented)

## Notable Quotes
(2-3 maximum, each under 15 words, formatted as blockquotes)

## Connections
(Bullet list using [[wikilinks]] to related concepts, fields, or thinkers
that connect to this book's ideas)

## Open Questions
(2-3 questions this book raises or leaves unanswered)
"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def sanitise_note_filename(name: str) -> str:
    """Convert a title into a safe filename for Obsidian (no path-illegal chars)."""
    name = unicodedata.normalize("NFKD", name)
    # Remove characters illegal in Windows filenames
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    # Collapse runs of underscores/spaces
    name = re.sub(r"[\s_]+", " ", name).strip()
    return name[:MAX_TITLE_LENGTH]


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "notes.log"

    logger = logging.getLogger("notes")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s"))
        logger.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(levelname)-7s  %(message)s"))
        logger.addHandler(ch)

    return logger


def load_book_text(book_dir: Path) -> str:
    """Concatenate all chapter .txt files, truncated to MAX_WORDS."""
    txt_files = sorted(book_dir.glob("*.txt"))
    parts = []
    word_count = 0

    for txt_file in txt_files:
        text = txt_file.read_text(encoding="utf-8", errors="replace")
        words = text.split()
        remaining = MAX_WORDS - word_count
        if remaining <= 0:
            break
        parts.append(" ".join(words[:remaining]))
        word_count += min(len(words), remaining)

    return "\n\n".join(parts)


def load_metadata(book_dir: Path) -> dict:
    """Load metadata.json for a book."""
    meta_file = book_dir / "metadata.json"
    if meta_file.exists():
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "title": book_dir.name,
        "author": "Unknown",
        "year": "Unknown",
        "format": "unknown",
    }


def generate_note(client: anthropic.Anthropic, metadata: dict, text: str) -> str:
    """Call Claude API to generate the Obsidian note."""
    user_prompt = (
        f"Book metadata: {json.dumps(metadata, ensure_ascii=False)}\n\n"
        f"Book text sample:\n{text}\n\n"
        f"{NOTE_TEMPLATE_INSTRUCTIONS}"
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    return response.content[0].text


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("Starting Obsidian note generation")

    # Check API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY environment variable not set.")
        print("ERROR: Set ANTHROPIC_API_KEY before running this script.")
        print("  Windows:  set ANTHROPIC_API_KEY=sk-ant-...")
        print("  Or:       $env:ANTHROPIC_API_KEY = 'sk-ant-...'  (PowerShell)")
        return

    client = anthropic.Anthropic(api_key=api_key)

    # Create output directory
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Output: %s", NOTES_DIR)

    # Gather book folders
    if not CHUNKS_DIR.exists():
        logger.error("Chunks directory not found: %s", CHUNKS_DIR)
        return

    book_dirs = sorted([d for d in CHUNKS_DIR.iterdir() if d.is_dir()])
    total = len(book_dirs)
    logger.info("Found %d books to process", total)

    success = 0
    skipped = 0
    failed = 0

    for i, book_dir in enumerate(book_dirs, 1):
        metadata = load_metadata(book_dir)
        title = metadata.get("title", book_dir.name)
        safe_filename = sanitise_note_filename(title)
        note_path = NOTES_DIR / f"{safe_filename}.md"

        # Idempotent: skip if note already exists
        if note_path.exists():
            skipped += 1
            continue

        print(f"[{i}/{total}] {title[:60]}")

        text = load_book_text(book_dir)
        if len(text.strip()) < 100:
            logger.warning("SKIP (too little text): %s", title)
            skipped += 1
            continue

        try:
            note_content = generate_note(client, metadata, text)
            note_path.write_text(note_content, encoding="utf-8")
            logger.info("OK: %s", title)
            success += 1
        except anthropic.APIError as e:
            logger.error("FAIL (API error): %s — %s", title, e)
            failed += 1
        except Exception as e:
            logger.error("FAIL: %s — %s", title, e)
            failed += 1

        # Rate limit delay
        time.sleep(API_DELAY_SECONDS)

    logger.info("-" * 60)
    logger.info("Done. Total: %d | Generated: %d | Skipped: %d | Failed: %d",
                total, success, skipped, failed)
    print(f"\nDone! Generated: {success} | Skipped: {skipped} | Failed: {failed}")


if __name__ == "__main__":
    main()
