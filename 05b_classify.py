"""
05b_classify.py — Classify books as fiction or non-fiction using Ollama.

For each book in PKM-Index/chunks/, sends the title + a short text sample
to a local Ollama model and asks for a one-word classification. Saves the
result as "genre" in each book's metadata.json.

Idempotent: skips books already classified (unless --reclassify is passed).
"""

import argparse
import json
import logging
import time
from pathlib import Path

import requests
from tqdm import tqdm

# ── Configuration ──────────────────────────────────────────────────────────────
INDEX_DIR = Path(r"N:\Projects\PKM\PKM-Index")
CHUNKS_DIR = INDEX_DIR / "chunks"
LOG_DIR = INDEX_DIR / "logs"

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "llama3.1:8b"
OLLAMA_TIMEOUT = 60         # Classification is fast — 60s is generous
SAMPLE_WORDS = 200          # Small sample is sufficient for genre detection

CLASSIFY_SYSTEM = (
    "You are a book classifier. You will be given the title and a short excerpt "
    "from a book. Classify it as fiction or non-fiction. Reply with EXACTLY one "
    "word: FICTION or NONFICTION. Nothing else."
)


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "classify.log"

    logger = logging.getLogger("classify")
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
    return {"title": book_dir.name}


def save_metadata(book_dir: Path, metadata: dict):
    meta_file = book_dir / "metadata.json"
    meta_file.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_text_sample(book_dir: Path) -> str:
    """Get a short text sample from the first chapter(s)."""
    txt_files = sorted(book_dir.glob("*.txt"))
    words = []
    for txt_file in txt_files:
        text = txt_file.read_text(encoding="utf-8", errors="replace")
        words.extend(text.split())
        if len(words) >= SAMPLE_WORDS:
            break
    return " ".join(words[:SAMPLE_WORDS])


def classify_book(title: str, sample: str, model: str = OLLAMA_MODEL) -> str:
    """Ask Ollama to classify a book. Returns 'fiction' or 'nonfiction'."""
    user_prompt = f"Title: {title}\n\nExcerpt:\n{sample}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": CLASSIFY_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {
            "num_predict": 10,     # Only need one word
            "num_ctx": 2048,       # Tiny context — fast
            "temperature": 0.1,    # Very deterministic
        },
    }

    resp = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
    resp.raise_for_status()
    answer = resp.json()["message"]["content"].strip().upper()

    # Parse the answer — be tolerant of slight variations
    if "NONFICTION" in answer or "NON-FICTION" in answer or "NON FICTION" in answer:
        return "nonfiction"
    elif "FICTION" in answer:
        return "fiction"
    else:
        # If ambiguous, default to nonfiction (safer for note generation)
        return "nonfiction"


def main():
    parser = argparse.ArgumentParser(
        description="Classify books as fiction or non-fiction using Ollama."
    )
    parser.add_argument(
        "--reclassify", action="store_true",
        help="Re-classify books that already have a genre tag"
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help=f"Override Ollama model (default: {OLLAMA_MODEL})"
    )
    args = parser.parse_args()

    model = args.model if args.model else OLLAMA_MODEL

    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("Starting fiction/non-fiction classification")
    logger.info("Model: %s", model)

    # Verify Ollama is running
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=5)
        r.raise_for_status()
    except requests.ConnectionError:
        print("ERROR: Cannot connect to Ollama at localhost:11434")
        return

    if not CHUNKS_DIR.exists():
        logger.error("Chunks directory not found: %s", CHUNKS_DIR)
        return

    book_dirs = sorted([d for d in CHUNKS_DIR.iterdir() if d.is_dir()])
    total = len(book_dirs)
    logger.info("Found %d books", total)

    classified = 0
    skipped = 0
    failed = 0
    fiction_count = 0
    nonfiction_count = 0

    for book_dir in tqdm(book_dirs, desc="Classifying", unit="book"):
        metadata = load_metadata(book_dir)
        title = metadata.get("title", book_dir.name)

        # Skip if already classified (unless --reclassify)
        if "genre" in metadata and not args.reclassify:
            skipped += 1
            continue

        sample = get_text_sample(book_dir)
        if len(sample.strip()) < 30:
            logger.warning("SKIP (too little text): %s", title)
            metadata["genre"] = "unknown"
            save_metadata(book_dir, metadata)
            skipped += 1
            continue

        try:
            genre = classify_book(title, sample, model)
            metadata["genre"] = genre
            save_metadata(book_dir, metadata)

            if genre == "fiction":
                fiction_count += 1
            else:
                nonfiction_count += 1

            classified += 1
            logger.info("%s: %s", genre.upper(), title[:80])
        except Exception as e:
            logger.error("FAIL: %s — %s", title[:60], e)
            failed += 1

        # Tiny delay to not overwhelm Ollama between requests
        time.sleep(0.2)

    logger.info("-" * 60)
    logger.info("Done. Classified: %d (fiction: %d, nonfiction: %d) | "
                "Skipped: %d | Failed: %d",
                classified, fiction_count, nonfiction_count, skipped, failed)
    print(f"\nDone! Classified: {classified} "
          f"(fiction: {fiction_count}, nonfiction: {nonfiction_count}) | "
          f"Skipped: {skipped} | Failed: {failed}")


if __name__ == "__main__":
    main()
