"""
03_build_index.py — Build a ChromaDB vector index from extracted text chunks.

Loads .txt chunk files from PKM-Index/chunks/, embeds them with
sentence-transformers (all-MiniLM-L6-v2), and stores in ChromaDB.
Idempotent: skips chunks already in the database (matched by document ID).
"""

import hashlib
import json
import logging
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer
from tqdm import tqdm

# ── Configuration ──────────────────────────────────────────────────────────────
INDEX_DIR = Path(r"N:\Projects\PKM\PKM-Index")
CHUNKS_DIR = INDEX_DIR / "chunks"
CHROMA_DIR = INDEX_DIR / "chroma"
LOG_DIR = INDEX_DIR / "logs"

MODEL_NAME = "all-MiniLM-L6-v2"
COLLECTION_NAME = "pkm_books"

# ChromaDB has a per-add batch limit; keep well under it
BATCH_SIZE = 64

# Maximum characters per chunk to embed (sentence-transformers has a 256 token
# window for this model — ~1200 chars covers most of it without truncation noise)
MAX_CHUNK_CHARS = 4000


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "index.log"

    logger = logging.getLogger("index")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s"))
        logger.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(levelname)-7s  %(message)s"))
        logger.addHandler(ch)

    return logger


def make_doc_id(book_folder: str, chunk_filename: str) -> str:
    """Deterministic document ID from book folder + chunk filename."""
    raw = f"{book_folder}/{chunk_filename}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_metadata(book_dir: Path) -> dict:
    """Load metadata.json for a book, returning defaults if missing."""
    meta_file = book_dir / "metadata.json"
    if meta_file.exists():
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "title": book_dir.name,
        "author": "Unknown",
        "source_path": "",
    }


def gather_chunks(logger: logging.Logger):
    """
    Walk all book folders in CHUNKS_DIR and yield dicts:
      { doc_id, text, metadata: {book_title, author, chapter_filename,
        chapter_num, source_path} }
    """
    if not CHUNKS_DIR.exists():
        logger.error("Chunks directory not found: %s", CHUNKS_DIR)
        return

    book_dirs = sorted([d for d in CHUNKS_DIR.iterdir() if d.is_dir()])
    logger.info("Found %d book folders in chunks/", len(book_dirs))

    for book_dir in book_dirs:
        meta = load_metadata(book_dir)
        txt_files = sorted(book_dir.glob("*.txt"))

        for idx, txt_file in enumerate(txt_files, 1):
            doc_id = make_doc_id(book_dir.name, txt_file.name)
            text = txt_file.read_text(encoding="utf-8", errors="replace")

            # Truncate very long chunks — the embedding model has a small window
            if len(text) > MAX_CHUNK_CHARS:
                text = text[:MAX_CHUNK_CHARS]

            if len(text.strip()) < 20:
                continue

            yield {
                "doc_id": doc_id,
                "text": text,
                "metadata": {
                    "book_title": meta.get("title", book_dir.name),
                    "author": meta.get("author", "Unknown"),
                    "chapter_filename": txt_file.name,
                    "chapter_num": idx,
                    "source_path": meta.get("source_path", ""),
                },
            }


def main():
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("Starting index build")

    # ── Load model ─────────────────────────────────────────────────────────
    logger.info("Loading embedding model: %s", MODEL_NAME)
    model = SentenceTransformer(MODEL_NAME)
    logger.info("Model loaded")

    # ── Init ChromaDB ──────────────────────────────────────────────────────
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    existing_count = collection.count()
    logger.info("ChromaDB collection '%s' has %d existing documents",
                COLLECTION_NAME, existing_count)

    # ── Get existing IDs for idempotency ───────────────────────────────────
    # Fetch all existing IDs so we can skip duplicates
    existing_ids = set()
    if existing_count > 0:
        # ChromaDB get() with no filter returns all; use peek for large sets
        # We'll fetch in pages
        page_size = 5000
        offset = 0
        while offset < existing_count:
            result = collection.get(
                limit=page_size,
                offset=offset,
                include=[],
            )
            existing_ids.update(result["ids"])
            offset += page_size
    logger.info("Loaded %d existing document IDs for dedup", len(existing_ids))

    # ── Gather and filter chunks ───────────────────────────────────────────
    all_chunks = list(gather_chunks(logger))
    new_chunks = [c for c in all_chunks if c["doc_id"] not in existing_ids]
    logger.info("Total chunks: %d | New (to index): %d | Already indexed: %d",
                len(all_chunks), len(new_chunks), len(all_chunks) - len(new_chunks))

    if not new_chunks:
        logger.info("Nothing new to index. Done.")
        print("Nothing new to index.")
        return

    # ── Embed and upsert in batches ────────────────────────────────────────
    added = 0
    failed = 0

    for i in tqdm(range(0, len(new_chunks), BATCH_SIZE),
                  desc="Indexing", unit="batch"):
        batch = new_chunks[i : i + BATCH_SIZE]
        texts = [c["text"] for c in batch]
        ids = [c["doc_id"] for c in batch]
        metadatas = [c["metadata"] for c in batch]

        try:
            embeddings = model.encode(texts, show_progress_bar=False).tolist()
            collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=texts,
                metadatas=metadatas,
            )
            added += len(batch)
        except Exception as e:
            logger.error("FAIL batch %d–%d: %s", i, i + len(batch), e)
            failed += len(batch)

    logger.info("-" * 60)
    logger.info("Done. Added: %d | Failed: %d | Total in DB: %d",
                added, failed, collection.count())
    print(f"\nDone! Added: {added} | Failed: {failed} | Total in DB: {collection.count()}")


if __name__ == "__main__":
    main()
