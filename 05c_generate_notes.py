"""
05c_generate_notes.py — Multi-pass MapReduce Obsidian note generation.

Pass 1 (Map):  For each chapter, send full text to Ollama → detailed
               bulleted notes per chapter.
Pass 2 (Reduce): Send all chapter notes to a synthesis call → overview,
                  cross-chapter themes, within-book linkages, concept network.

This means the LLM reads the ENTIRE book (not just a truncated sample).

Options:
  --nonfiction-only   Only process non-fiction books (default)
  --all               Process all books regardless of genre
  --fiction-only       Only process fiction books
  --time-limit N      Stop after N minutes (default: 180 = 3 hours)
  --resume            Re-process books that already have notes
  --claude            Use Claude API instead of Ollama
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

# Ollama settings
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "llama3.1:8b"
OLLAMA_TIMEOUT = 600
OLLAMA_NUM_CTX = 8192       # Per-chapter calls use small context
OLLAMA_SYNTH_CTX = 16384    # Synthesis call uses larger context

# Claude settings
CLAUDE_MODEL = "claude-sonnet-4-20250514"

# Chunking: if a single chapter exceeds this, split it into sub-chunks
MAX_WORDS_PER_CHAPTER_CALL = 2500   # ~3,300 tokens — sweet spot per cognitivetech
MAX_CHAPTER_NOTES_WORDS = 8000      # Cap total chapter notes for synthesis call

MAX_TITLE_LENGTH = 80
DEFAULT_TIME_LIMIT_MIN = 180        # 3 hours


# ── Prompts ────────────────────────────────────────────────────────────────────

MAP_SYSTEM = (
    "You are a meticulous note-taking assistant. Given a chapter from a book, "
    "write comprehensive bulleted notes. Use headings and **bold** for key terms. "
    "Capture all important concepts, arguments, evidence, and frameworks. "
    "Output ONLY the bulleted notes, no preamble."
)

MAP_USER = """\
Book: {title} by {author}
Chapter: {chapter_name} (Chapter {chapter_num} of {total_chapters})

Text:
{text}

Write detailed bulleted notes for this chapter. Include:
- All key concepts and terms (in **bold**)
- Main arguments or claims with supporting evidence
- Any frameworks, models, or step-by-step processes
- Important names, dates, or data points
- How this chapter connects to the book's overall thesis
"""

REDUCE_SYSTEM = (
    "You are a knowledge extraction assistant. Given detailed chapter-by-chapter "
    "notes from a book, generate a comprehensive structured Obsidian markdown note. "
    "Output ONLY valid markdown with YAML frontmatter. No preamble, no explanation, "
    "no code fences. Start directly with --- of the YAML frontmatter."
)

REDUCE_TEMPLATE = """\
Book metadata: {metadata_json}

Here are the detailed chapter-by-chapter notes:

{chapter_notes}

Generate a comprehensive Obsidian markdown note with this exact structure.
Use [[wikilinks]] for EVERY important concept, person, framework, or term.
Be specific and substantive. This should read like detailed student study notes.

---
title: "{title}"
author: "{author}"
year: {year}
tags: [tag1, tag2, tag3, tag4, tag5]
format: {format}
genre: {genre}
read_status: "unread"
anki_exported: false
chapter_count: {chapter_count}
---

# {title}

## Overview
(4-5 sentences: core thesis, methodology, who it's for, what makes it distinctive)

## Chapter-by-Chapter Notes
(For EACH chapter, write a ### heading and 5-8 detailed bullet points.
Use [[wikilinks]] for every key concept. Be thorough — this is the main
reference section. Example:

### Chapter 1: <topic>
- Introduces [[Core Concept]] as the foundation for the book's argument
- Argues that [[Framework X]] addresses the gap in [[Field Y]]
- Key evidence: ...
- Defines [[Term Z]] as "..." which recurs throughout the book
...
)

## Cross-Chapter Themes
(Bullet list of 5-10 recurring themes/concepts that appear across multiple chapters.
For each, note WHICH chapters and how the concept evolves.
Example:
- [[Systems Thinking]] — introduced in Ch.1, applied to organizations in Ch.4,
  extended to personal life in Ch.8, synthesized with [[Complexity Theory]] in Ch.12
)

## Key Frameworks and Models
(Every named framework, model, or process the author presents. Give each a
[[wikilink]] and a 1-2 sentence description of how it works.)

## Within-Book Linkages
(Map how ideas BUILD on each other. Show the intellectual architecture.
Example:
- The [[OODA Loop]] from Ch.2 is applied to [[Decision Making Under Uncertainty]] in Ch.6
- [[Scarcity Mindset]] (Ch.1) contrasts with [[Abundance Mindset]] (Ch.4),
  resolved through [[Cognitive Reframing]] in Ch.7
)

## Key Evidence and Data
(Notable statistics, studies, experiments, or case studies cited in the book)

## Notable Quotes
(5-8 quotes, each under 25 words, as blockquotes. Note chapter.)

## Connections to Other Works
([[wikilinks]] to related books, thinkers, theories, or fields)

## Open Questions
(5+ questions this book raises, leaves unanswered, or that deserve follow-up)

## Personal Action Items
(3-5 concrete takeaways — what could the reader DO with this knowledge?)
"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def sanitise_note_filename(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"[\s_]+", " ", name).strip()
    return name[:MAX_TITLE_LENGTH]


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "notes_v2.log"

    logger = logging.getLogger("notes_v2")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s"))
        logger.addHandler(fh)
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(levelname)-7s  %(message)s"))
        logger.addHandler(ch)

    return logger


def load_metadata(book_dir: Path) -> dict:
    meta_file = book_dir / "metadata.json"
    if meta_file.exists():
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "title": book_dir.name, "author": "Unknown",
        "year": "Unknown", "format": "unknown",
        "genre": "unknown", "chapter_count": 0,
    }


def load_chapters(book_dir: Path) -> list[dict]:
    """Load all chapter .txt files with their full text."""
    txt_files = sorted(book_dir.glob("*.txt"))
    chapters = []
    for txt_file in txt_files:
        text = txt_file.read_text(encoding="utf-8", errors="replace").strip()
        if len(text) < 50:
            continue
        chapters.append({"name": txt_file.stem, "text": text})
    return chapters


def chunk_text(text: str, max_words: int) -> list[str]:
    """Split text into chunks of max_words."""
    words = text.split()
    if len(words) <= max_words:
        return [text]
    chunks = []
    for i in range(0, len(words), max_words):
        chunks.append(" ".join(words[i:i + max_words]))
    return chunks


def clean_response(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


# ── LLM Calls ─────────────────────────────────────────────────────────────────

def call_ollama(system: str, user: str, num_ctx: int = None,
                max_tokens: int = 2000) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {
            "num_predict": max_tokens,
            "num_ctx": num_ctx or OLLAMA_NUM_CTX,
            "temperature": 0.3,
        },
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def call_claude(client, system: str, user: str, max_tokens: int = 4000) -> str:
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text


# ── MapReduce Pipeline ─────────────────────────────────────────────────────────

def map_chapter(chapter: dict, chapter_num: int, total_chapters: int,
                metadata: dict, use_claude: bool = False,
                claude_client=None) -> str:
    """Pass 1: Generate detailed notes for a single chapter."""
    text_chunks = chunk_text(chapter["text"], MAX_WORDS_PER_CHAPTER_CALL)
    all_notes = []

    for i, chunk in enumerate(text_chunks):
        suffix = f" (part {i+1}/{len(text_chunks)})" if len(text_chunks) > 1 else ""
        user_prompt = MAP_USER.format(
            title=metadata.get("title", "Unknown"),
            author=metadata.get("author", "Unknown"),
            chapter_name=chapter["name"] + suffix,
            chapter_num=chapter_num,
            total_chapters=total_chapters,
            text=chunk,
        )

        if use_claude:
            notes = call_claude(claude_client, MAP_SYSTEM, user_prompt,
                                max_tokens=2000)
        else:
            notes = call_ollama(MAP_SYSTEM, user_prompt,
                                num_ctx=OLLAMA_NUM_CTX, max_tokens=2000)
        all_notes.append(notes)

    return "\n\n".join(all_notes)


def reduce_synthesize(chapter_notes_text: str, metadata: dict,
                      use_claude: bool = False, claude_client=None) -> str:
    """Pass 2: Synthesize all chapter notes into final Obsidian note."""
    user_prompt = REDUCE_TEMPLATE.format(
        metadata_json=json.dumps(metadata, ensure_ascii=False),
        chapter_notes=chapter_notes_text,
        title=metadata.get("title", "Unknown"),
        author=metadata.get("author", "Unknown"),
        year=metadata.get("year", "Unknown"),
        format=metadata.get("format", "unknown"),
        genre=metadata.get("genre", "unknown"),
        chapter_count=metadata.get("chapter_count", 0),
    )

    if use_claude:
        result = call_claude(claude_client, REDUCE_SYSTEM, user_prompt,
                             max_tokens=6000)
    else:
        result = call_ollama(REDUCE_SYSTEM, user_prompt,
                             num_ctx=OLLAMA_SYNTH_CTX, max_tokens=4000)

    return clean_response(result)


def process_book(book_dir: Path, metadata: dict, logger: logging.Logger,
                 use_claude: bool = False, claude_client=None) -> str | None:
    """Full MapReduce pipeline for one book. Returns note content or None."""
    chapters = load_chapters(book_dir)
    if not chapters:
        logger.warning("SKIP (no chapters): %s", metadata.get("title"))
        return None

    title = metadata.get("title", "Unknown")
    total = len(chapters)
    metadata["chapter_count"] = total

    # ── Pass 1: Map — notes per chapter ─────────────────────────────────
    logger.info("  Pass 1: Mapping %d chapters...", total)
    chapter_notes = []

    for i, chapter in enumerate(chapters, 1):
        word_count = len(chapter["text"].split())
        n_chunks = math.ceil(word_count / MAX_WORDS_PER_CHAPTER_CALL)
        calls_label = f" ({n_chunks} calls)" if n_chunks > 1 else ""
        print(f"    Ch.{i}/{total}: {chapter['name']}"
              f" [{word_count:,} words]{calls_label}")

        try:
            notes = map_chapter(chapter, i, total, metadata,
                                use_claude, claude_client)
            chapter_notes.append(
                f"### Chapter {i}: {chapter['name']}\n{notes}"
            )
        except Exception as e:
            logger.error("  FAIL mapping ch.%d: %s", i, e)
            chapter_notes.append(
                f"### Chapter {i}: {chapter['name']}\n"
                f"(Notes could not be generated: {e})"
            )

    all_chapter_notes = "\n\n".join(chapter_notes)

    # ── Trim chapter notes if they exceed the synthesis context budget ───
    words = all_chapter_notes.split()
    if len(words) > MAX_CHAPTER_NOTES_WORDS:
        logger.info("  Trimming chapter notes from %d to %d words for synthesis",
                     len(words), MAX_CHAPTER_NOTES_WORDS)
        all_chapter_notes = " ".join(words[:MAX_CHAPTER_NOTES_WORDS])

    # ── Pass 2: Reduce — synthesis ──────────────────────────────────────
    logger.info("  Pass 2: Synthesizing final note (%d words of chapter notes)...",
                len(all_chapter_notes.split()))

    try:
        note_content = reduce_synthesize(all_chapter_notes, metadata,
                                          use_claude, claude_client)
        return note_content
    except Exception as e:
        logger.error("  FAIL synthesis: %s", e)
        # Fallback: return just the chapter notes as a raw note
        fallback = (
            f"---\ntitle: \"{title}\"\nauthor: \"{metadata.get('author', 'Unknown')}\"\n"
            f"tags: [needs-synthesis]\nread_status: \"unread\"\n---\n\n"
            f"# {title}\n\n"
            f"*Synthesis failed — raw chapter notes below:*\n\n"
            f"{all_chapter_notes}"
        )
        return fallback


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Multi-pass MapReduce Obsidian note generation."
    )
    parser.add_argument("--claude", action="store_true",
                        help="Use Claude API instead of Ollama")
    parser.add_argument("--model", type=str, default=None,
                        help="Override model name")
    parser.add_argument("--nonfiction-only", action="store_true", default=True,
                        help="Only process non-fiction books (default)")
    parser.add_argument("--fiction-only", action="store_true",
                        help="Only process fiction books")
    parser.add_argument("--all", dest="all_genres", action="store_true",
                        help="Process all books regardless of genre")
    parser.add_argument("--time-limit", type=int, default=DEFAULT_TIME_LIMIT_MIN,
                        help=f"Stop after N minutes (default: {DEFAULT_TIME_LIMIT_MIN})")
    parser.add_argument("--resume", action="store_true",
                        help="Re-process books that already have notes")
    args = parser.parse_args()

    logger = setup_logging()
    logger.info("=" * 60)

    # ── Backend setup ──────────────────────────────────────────────────
    global OLLAMA_MODEL
    use_claude = args.claude
    claude_client = None

    if use_claude:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("ERROR: Set ANTHROPIC_API_KEY for --claude mode.")
            return
        claude_client = anthropic.Anthropic(api_key=api_key)
        model_name = args.model or CLAUDE_MODEL
        logger.info("Backend: Claude API (%s)", model_name)
    else:
        if args.model:
            OLLAMA_MODEL = args.model
        try:
            r = requests.get("http://localhost:11434/api/tags", timeout=5)
            r.raise_for_status()
        except requests.ConnectionError:
            print("ERROR: Cannot connect to Ollama at localhost:11434")
            return
        logger.info("Backend: Ollama local (%s)", OLLAMA_MODEL)

    # ── Genre filter ───────────────────────────────────────────────────
    if args.all_genres:
        genre_filter = None
        genre_label = "all genres"
    elif args.fiction_only:
        genre_filter = "fiction"
        genre_label = "fiction only"
    else:
        genre_filter = "nonfiction"
        genre_label = "non-fiction only"

    logger.info("Genre filter: %s", genre_label)
    logger.info("Time limit: %d minutes", args.time_limit)

    # ── Gather books ───────────────────────────────────────────────────
    NOTES_DIR.mkdir(parents=True, exist_ok=True)

    if not CHUNKS_DIR.exists():
        logger.error("Chunks directory not found")
        return

    all_book_dirs = sorted([d for d in CHUNKS_DIR.iterdir() if d.is_dir()])

    # Filter by genre
    book_dirs = []
    for d in all_book_dirs:
        meta = load_metadata(d)
        genre = meta.get("genre", "unknown")
        if genre_filter and genre != genre_filter:
            continue
        book_dirs.append((d, meta))

    total = len(book_dirs)
    logger.info("Books to process (%s): %d of %d total",
                genre_label, total, len(all_book_dirs))

    if total == 0:
        print(f"No books match filter '{genre_label}'. "
              f"Run 05b_classify.py first to tag genres.")
        return

    # ── Process loop with time limit ───────────────────────────────────
    start_time = time.time()
    time_limit_sec = args.time_limit * 60

    success = 0
    skipped = 0
    failed = 0

    for i, (book_dir, metadata) in enumerate(book_dirs, 1):
        # Check time limit
        elapsed = time.time() - start_time
        if elapsed >= time_limit_sec:
            remaining = total - i + 1
            logger.info("TIME LIMIT reached (%d min). Stopping. "
                        "%d books remaining.",
                        args.time_limit, remaining)
            print(f"\n⏰ Time limit ({args.time_limit} min) reached. "
                  f"{remaining} books remaining. Run again to continue.")
            break

        title = metadata.get("title", book_dir.name)
        safe_filename = sanitise_note_filename(title)
        note_path = NOTES_DIR / f"{safe_filename}.md"

        # Skip existing (unless --resume)
        if note_path.exists() and not args.resume:
            skipped += 1
            continue

        n_chapters = len(list(book_dir.glob("*.txt")))
        elapsed_min = (time.time() - start_time) / 60
        print(f"\n[{i}/{total}] ({elapsed_min:.0f}m elapsed) "
              f"{title[:55]} ({n_chapters} chapters)")

        note_content = process_book(book_dir, metadata, logger,
                                     use_claude, claude_client)

        if note_content:
            note_path.write_text(note_content, encoding="utf-8")
            logger.info("OK: %s (%d chapters)", title[:60], n_chapters)
            success += 1
        else:
            failed += 1

    # ── Summary ────────────────────────────────────────────────────────
    elapsed_min = (time.time() - start_time) / 60
    logger.info("-" * 60)
    logger.info("Session complete in %.1f min. Generated: %d | Skipped: %d | "
                "Failed: %d", elapsed_min, success, skipped, failed)
    print(f"\nSession complete ({elapsed_min:.0f} min). "
          f"Generated: {success} | Skipped: {skipped} | Failed: {failed}")


if __name__ == "__main__":
    main()
