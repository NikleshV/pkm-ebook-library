"""
05_generate_notes.py — Generate detailed structured Obsidian notes for each book.

Default: uses Ollama (llama3.1:8b) running locally — completely free.
Optional: pass --claude flag to use Anthropic Claude API instead
          (requires ANTHROPIC_API_KEY environment variable).

Features:
  - Chapter-by-chapter notes with per-chapter key points
  - Within-book linkages mapped as [[wikilinks]] across chapters
  - Batch processing: --batch N --batch-size 300 to process in chunks
  - Idempotent: skips books whose note already exists
"""

import argparse
import json
import logging
import math
import os
import re
import time
import unicodedata
from pathlib import Path

import requests

# ── Configuration ──────────────────────────────────────────────────────────────
INDEX_DIR = Path(r"N:\Projects\PKM\PKM-Index")
CHUNKS_DIR = INDEX_DIR / "chunks"
LOG_DIR = INDEX_DIR / "logs"

OBSIDIAN_VAULT = Path(
    r"C:\Users\Niklesh\OneDrive\Documents\MD Obsidian\Niklesh Notes_Main"
)
NOTES_DIR = OBSIDIAN_VAULT / "PKM-Library" / "Books"

# Ollama settings (default — free, local)
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "llama3.1:8b"
OLLAMA_TIMEOUT = 600        # 10 min safety net
OLLAMA_NUM_CTX = 16384      # 16k context window
OLLAMA_MAX_WORDS = 6000     # distributed evenly across chapters
OLLAMA_MAX_OUTPUT = 4000    # tokens — detailed notes need more room

# Claude settings (optional — paid)
CLAUDE_MODEL = "claude-sonnet-4-20250514"
CLAUDE_MAX_WORDS = 6000
CLAUDE_MAX_OUTPUT = 4000

# Words per chapter sample when distributing budget across chapters
WORDS_PER_CHAPTER = 400     # ~530 tokens; keeps each chapter well-represented

API_DELAY_SECONDS = 1       # 1s Ollama, 3s Claude
MAX_TITLE_LENGTH = 80

SYSTEM_PROMPT = (
    "You are a knowledge extraction assistant. Given the text of a book — "
    "organised by chapter — generate a detailed, structured Obsidian markdown note. "
    "Output ONLY valid markdown with YAML frontmatter. No preamble, no explanation, "
    "no wrapping in code fences. Start directly with --- of the YAML frontmatter."
)

NOTE_TEMPLATE_INSTRUCTIONS = """\
Generate a detailed Obsidian markdown note with this exact structure.
Use [[wikilinks]] for every important concept, person, framework, or term
that could link to another note. Be specific and substantive in every section.

---
title: "{title}"
author: "{author}"
year: {year}
tags: [tag1, tag2, tag3]
format: {format}
read_status: "unread"
anki_exported: false
chapter_count: {chapter_count}
---

# {title}

## Overview
(3-4 sentences: core thesis, who it is for, and why it matters)

## Chapter Notes
(For each chapter in the text, write a ### heading and 3-5 bullet points
capturing the key ideas, arguments, or facts from that chapter.
Use [[wikilinks]] for any concept worth linking. Example:

### Chapter 1: <title or topic>
- Key point introducing [[Core Concept]]
- How [[Framework X]] is established here
...

### Chapter 2: <title or topic>
- ...
)

## Cross-Chapter Themes
(Bullet list of recurring themes or concepts that appear across multiple chapters.
For each, note WHICH chapters it appears in and use [[wikilinks]].
Example:
- [[Systems Thinking]] — central to Chapters 1, 3, and 5; evolves from intro to application
)

## Key Frameworks or Models
(Bullet list of named frameworks, models, or step-by-step processes the author presents.
Give each a [[wikilink]] and a one-line description.)

## Within-Book Linkages
(Map how ideas BUILD on each other across the book.
Example:
- The [[OODA Loop]] introduced in Ch.2 is applied to [[Decision Making Under Uncertainty]] in Ch.6
- [[Scarcity Mindset]] (Ch.1) contrasts with [[Abundance Mindset]] (Ch.4), resolved in Ch.7
)

## Notable Quotes
(3-5 quotes, each under 20 words, as blockquotes. Note chapter if known.)

## Connections to Other Works
([[wikilinks]] to related books, thinkers, or fields that connect to this book's ideas)

## Open Questions
(3-5 questions this book raises, leaves unanswered, or that deserve follow-up)
"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def sanitise_note_filename(name: str) -> str:
    """Convert a title into a safe filename for Obsidian."""
    name = unicodedata.normalize("NFKD", name)
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
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


def load_chapters(book_dir: Path, max_words: int) -> list[dict]:
    """
    Load each chapter .txt file as a separate dict {name, text}.
    Distributes the word budget evenly across all chapters so every
    chapter is represented rather than front-loading early chapters.
    """
    txt_files = sorted(book_dir.glob("*.txt"))
    if not txt_files:
        return []

    n_chapters = len(txt_files)
    # Each chapter gets an equal share, capped at WORDS_PER_CHAPTER
    words_each = min(WORDS_PER_CHAPTER, max_words // n_chapters)

    chapters = []
    for txt_file in txt_files:
        raw = txt_file.read_text(encoding="utf-8", errors="replace")
        words = raw.split()
        snippet = " ".join(words[:words_each])
        if len(snippet.strip()) < 30:
            continue
        chapters.append({
            "name": txt_file.stem,       # e.g. "chapter_001" or "section_002_pages_21_to_40"
            "text": snippet,
        })

    return chapters


def format_chapters_for_prompt(chapters: list[dict]) -> str:
    """Format chapter list into a clearly delimited string for the prompt."""
    parts = []
    for i, ch in enumerate(chapters, 1):
        parts.append(f"=== Chapter {i} ({ch['name']}) ===\n{ch['text']}")
    return "\n\n".join(parts)


def load_metadata(book_dir: Path) -> dict:
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
        "chapter_count": 0,
    }


def clean_response(text: str) -> str:
    """Strip markdown code fences the model might wrap around output."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def build_user_prompt(metadata: dict, chapters: list[dict]) -> str:
    chapter_text = format_chapters_for_prompt(chapters)
    return (
        f"Book metadata: {json.dumps(metadata, ensure_ascii=False)}\n\n"
        f"Book text (by chapter):\n{chapter_text}\n\n"
        f"{NOTE_TEMPLATE_INSTRUCTIONS}"
    )


# ── LLM Backends ──────────────────────────────────────────────────────────────

def generate_note_ollama(metadata: dict, chapters: list[dict]) -> str:
    """Generate note via Ollama (local, free)."""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(metadata, chapters)},
        ],
        "stream": False,
        "options": {
            "num_predict": OLLAMA_MAX_OUTPUT,
            "num_ctx": OLLAMA_NUM_CTX,
            "temperature": 0.3,
        },
    }

    resp = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
    resp.raise_for_status()
    return clean_response(resp.json()["message"]["content"])


def generate_note_claude(client, metadata: dict, chapters: list[dict]) -> str:
    """Generate note via Anthropic Claude API (paid)."""
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=CLAUDE_MAX_OUTPUT,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_prompt(metadata, chapters)}],
    )
    return clean_response(response.content[0].text)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate detailed Obsidian notes from extracted book text."
    )
    parser.add_argument(
        "--claude", action="store_true",
        help="Use Claude API instead of Ollama (requires ANTHROPIC_API_KEY)"
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Override the model name"
    )
    parser.add_argument(
        "--batch", type=int, default=None,
        help="Which batch to process (1-indexed). Use with --batch-size."
    )
    parser.add_argument(
        "--batch-size", type=int, default=300,
        help="Number of books per batch (default: 300)"
    )
    args = parser.parse_args()

    logger = setup_logging()
    logger.info("=" * 60)

    # ── Select backend ─────────────────────────────────────────────
    use_claude = args.claude
    claude_client = None

    if use_claude:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            logger.error("ANTHROPIC_API_KEY not set.")
            print("ERROR: Set ANTHROPIC_API_KEY before running with --claude.")
            return
        claude_client = anthropic.Anthropic(api_key=api_key)
        model_name = args.model or CLAUDE_MODEL
        max_words = CLAUDE_MAX_WORDS
        delay = 3
        logger.info("Backend: Claude API (%s)", model_name)
    else:
        model_name = args.model or OLLAMA_MODEL
        max_words = OLLAMA_MAX_WORDS
        try:
            r = requests.get("http://localhost:11434/api/tags", timeout=5)
            r.raise_for_status()
            available = [m["name"] for m in r.json().get("models", [])]
            if model_name not in available and f"{model_name}:latest" not in available:
                print(f"WARNING: Model '{model_name}' not found in Ollama.")
                print(f"  Available: {', '.join(available)}")
                return
        except requests.ConnectionError:
            print("ERROR: Cannot connect to Ollama at localhost:11434")
            return
        delay = 1
        logger.info("Backend: Ollama local (%s) — FREE", model_name)

    # Create output directory
    NOTES_DIR.mkdir(parents=True, exist_ok=True)

    # Gather all book folders
    if not CHUNKS_DIR.exists():
        logger.error("Chunks directory not found: %s", CHUNKS_DIR)
        return

    all_book_dirs = sorted([d for d in CHUNKS_DIR.iterdir() if d.is_dir()])
    total_books = len(all_book_dirs)

    # ── Apply batching ─────────────────────────────────────────────
    if args.batch is not None:
        batch_size = args.batch_size
        total_batches = math.ceil(total_books / batch_size)
        batch_num = args.batch

        if batch_num < 1 or batch_num > total_batches:
            print(f"ERROR: --batch must be between 1 and {total_batches} "
                  f"(you have {total_books} books, batch-size {batch_size})")
            return

        start_idx = (batch_num - 1) * batch_size
        end_idx = min(start_idx + batch_size, total_books)
        book_dirs = all_book_dirs[start_idx:end_idx]

        print(f"\nBatch {batch_num}/{total_batches}: "
              f"books {start_idx + 1}–{end_idx} of {total_books}")
        logger.info("Batch %d/%d: books %d–%d of %d",
                    batch_num, total_batches, start_idx + 1, end_idx, total_books)
    else:
        book_dirs = all_book_dirs
        print(f"\nProcessing all {total_books} books (no batch selected)")

    logger.info("Starting note generation — output: %s", NOTES_DIR)

    success = 0
    skipped = 0
    failed = 0
    batch_total = len(book_dirs)

    for i, book_dir in enumerate(book_dirs, 1):
        metadata = load_metadata(book_dir)
        title = metadata.get("title", book_dir.name)
        safe_filename = sanitise_note_filename(title)
        note_path = NOTES_DIR / f"{safe_filename}.md"

        if note_path.exists():
            skipped += 1
            continue

        print(f"[{i}/{batch_total}] {title[:65]}")

        chapters = load_chapters(book_dir, max_words)
        if not chapters:
            logger.warning("SKIP (no chapters): %s", title)
            skipped += 1
            continue

        # Inject chapter_count into metadata for the template
        metadata["chapter_count"] = len(chapters)

        try:
            if use_claude:
                note_content = generate_note_claude(claude_client, metadata, chapters)
            else:
                note_content = generate_note_ollama(metadata, chapters)

            note_path.write_text(note_content, encoding="utf-8")
            logger.info("OK (%d chapters): %s", len(chapters), title)
            success += 1
        except Exception as e:
            logger.error("FAIL: %s — %s", title, e)
            failed += 1

        time.sleep(delay)

    logger.info("-" * 60)
    logger.info("Done. Processed: %d | Generated: %d | Skipped: %d | Failed: %d",
                batch_total, success, skipped, failed)
    print(f"\nDone! Generated: {success} | Skipped: {skipped} | Failed: {failed}")


if __name__ == "__main__":
    main()
