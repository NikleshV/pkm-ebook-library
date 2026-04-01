"""
05_generate_notes.py — Generate structured Obsidian notes for each book.

Default: uses Ollama (llama3.1:8b) running locally — completely free.
Optional: pass --claude flag to use Anthropic Claude API instead
          (requires ANTHROPIC_API_KEY environment variable).

Reads extracted chapter text from PKM-Index/chunks/, sends to the LLM
with a knowledge-extraction prompt, and saves the resulting markdown note
to the Obsidian vault. Idempotent: skips books whose note already exists.
"""

import argparse
import json
import logging
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
OLLAMA_TIMEOUT = 600        # 10 min — generous safety net
OLLAMA_NUM_CTX = 16384      # 16k context — 6k words in + 2k out, with headroom
OLLAMA_MAX_WORDS = 6000     # ~8,000 tokens — same as Claude now

# Claude settings (optional — paid)
CLAUDE_MODEL = "claude-sonnet-4-20250514"
CLAUDE_MAX_WORDS = 6000

API_DELAY_SECONDS = 1       # 1s for local Ollama, bumped to 3s for Claude
MAX_TITLE_LENGTH = 80
# (MAX_WORDS is now per-backend: OLLAMA_MAX_WORDS / CLAUDE_MAX_WORDS)

SYSTEM_PROMPT = (
    "You are a knowledge extraction assistant. Given the text of a book, "
    "generate a structured Obsidian markdown note. Output ONLY valid markdown "
    "with YAML frontmatter. No preamble, no explanation, no wrapping in "
    "code fences. Start directly with the --- of the YAML frontmatter."
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


def load_book_text(book_dir: Path, max_words: int) -> str:
    """Concatenate all chapter .txt files, truncated to max_words."""
    txt_files = sorted(book_dir.glob("*.txt"))
    parts = []
    word_count = 0

    for txt_file in txt_files:
        text = txt_file.read_text(encoding="utf-8", errors="replace")
        words = text.split()
        remaining = max_words - word_count
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


def clean_response(text: str) -> str:
    """Strip any markdown code fences the model might wrap around the output."""
    text = text.strip()
    # Remove ```markdown ... ``` wrapping
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```markdown or ```)
        lines = lines[1:]
        # Remove last line if it's ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


# ── LLM Backends ──────────────────────────────────────────────────────────────

def generate_note_ollama(metadata: dict, text: str) -> str:
    """Generate note via Ollama (local, free)."""
    user_prompt = (
        f"Book metadata: {json.dumps(metadata, ensure_ascii=False)}\n\n"
        f"Book text sample:\n{text}\n\n"
        f"{NOTE_TEMPLATE_INSTRUCTIONS}"
    )

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {
            "num_predict": 2000,
            "num_ctx": OLLAMA_NUM_CTX,   # explicit context window
            "temperature": 0.3,
        },
    }

    resp = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
    resp.raise_for_status()
    content = resp.json()["message"]["content"]
    return clean_response(content)


def generate_note_claude(client, metadata: dict, text: str) -> str:
    """Generate note via Anthropic Claude API (paid)."""
    user_prompt = (
        f"Book metadata: {json.dumps(metadata, ensure_ascii=False)}\n\n"
        f"Book text sample:\n{text}\n\n"
        f"{NOTE_TEMPLATE_INSTRUCTIONS}"
    )

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    return clean_response(response.content[0].text)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate Obsidian notes from extracted book text."
    )
    parser.add_argument(
        "--claude", action="store_true",
        help="Use Anthropic Claude API instead of local Ollama (requires ANTHROPIC_API_KEY)"
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help=f"Override the model name (default: {OLLAMA_MODEL} for Ollama, "
             f"{CLAUDE_MODEL} for Claude)"
    )
    args = parser.parse_args()

    logger = setup_logging()
    logger.info("=" * 60)

    # ── Select backend ─────────────────────────────────────────────────────
    use_claude = args.claude
    claude_client = None

    if use_claude:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            logger.error("ANTHROPIC_API_KEY environment variable not set.")
            print("ERROR: Set ANTHROPIC_API_KEY before running with --claude.")
            print("  Windows:  set ANTHROPIC_API_KEY=sk-ant-...")
            print("  Or:       $env:ANTHROPIC_API_KEY = 'sk-ant-...'  (PowerShell)")
            return
        claude_client = anthropic.Anthropic(api_key=api_key)
        model_name = args.model or CLAUDE_MODEL
        delay = 3
        logger.info("Backend: Claude API (%s)", model_name)
    else:
        # Verify Ollama is running
        model_name = args.model or OLLAMA_MODEL
        try:
            r = requests.get("http://localhost:11434/api/tags", timeout=5)
            r.raise_for_status()
            available = [m["name"] for m in r.json().get("models", [])]
            if model_name not in available and f"{model_name}:latest" not in available:
                print(f"WARNING: Model '{model_name}' not found in Ollama.")
                print(f"  Available: {', '.join(available)}")
                print(f"  Run: ollama pull {model_name}")
                return
        except requests.ConnectionError:
            print("ERROR: Cannot connect to Ollama at localhost:11434")
            print("  Make sure Ollama is running: ollama serve")
            return
        delay = 1
        logger.info("Backend: Ollama local (%s) — FREE", model_name)

    logger.info("Starting Obsidian note generation")

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

        max_words = CLAUDE_MAX_WORDS if use_claude else OLLAMA_MAX_WORDS
        text = load_book_text(book_dir, max_words)
        if len(text.strip()) < 100:
            logger.warning("SKIP (too little text): %s", title)
            skipped += 1
            continue

        try:
            if use_claude:
                note_content = generate_note_claude(claude_client, metadata, text)
            else:
                note_content = generate_note_ollama(metadata, text)

            note_path.write_text(note_content, encoding="utf-8")
            logger.info("OK: %s", title)
            success += 1
        except Exception as e:
            logger.error("FAIL: %s — %s", title, e)
            failed += 1

        time.sleep(delay)

    logger.info("-" * 60)
    logger.info("Done. Total: %d | Generated: %d | Skipped: %d | Failed: %d",
                total, success, skipped, failed)
    print(f"\nDone! Generated: {success} | Skipped: {skipped} | Failed: {failed}")


if __name__ == "__main__":
    main()
