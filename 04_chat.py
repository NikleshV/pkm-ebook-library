"""
04_chat.py — Terminal semantic search over your book library.

Type a question, get the top 8 most relevant chunks from ChromaDB
with source attribution (book title, author, chapter). Pure retrieval,
no LLM call. Type 'quit' to exit.
"""

from pathlib import Path
from textwrap import shorten

import chromadb
from sentence_transformers import SentenceTransformer

# ── Configuration ──────────────────────────────────────────────────────────────
INDEX_DIR = Path(r"N:\Projects\PKM\PKM-Index")
CHROMA_DIR = INDEX_DIR / "chroma"

MODEL_NAME = "all-MiniLM-L6-v2"
COLLECTION_NAME = "pkm_books"
TOP_K = 8
PREVIEW_CHARS = 400


def print_banner():
    print("\n" + "=" * 64)
    print("  📚 PKM Library Search  —  36,924 chunks indexed")
    print("  Type a question to search your books. Type 'quit' to exit.")
    print("=" * 64 + "\n")


def format_result(rank: int, doc: str, meta: dict, distance: float) -> str:
    """Format a single search result for display."""
    title = meta.get("book_title", "Unknown")
    author = meta.get("author", "Unknown")
    chapter = meta.get("chapter_filename", "?")
    similarity = max(0, 1 - distance)  # cosine distance → similarity

    preview = doc.replace("\n", " ").strip()
    preview = shorten(preview, width=PREVIEW_CHARS, placeholder=" …")

    return (
        f"\n{'─' * 64}\n"
        f"  [{rank}]  {title}\n"
        f"        Author: {author}  |  Chapter: {chapter}  |  "
        f"Score: {similarity:.3f}\n"
        f"{'─' * 64}\n"
        f"  {preview}\n"
    )


def main():
    # ── Load model and DB ──────────────────────────────────────────────────
    print("Loading embedding model…")
    model = SentenceTransformer(MODEL_NAME)

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_collection(name=COLLECTION_NAME)

    doc_count = collection.count()
    print(f"Connected to ChromaDB: {doc_count:,} chunks available\n")

    print_banner()

    # ── Chat loop ──────────────────────────────────────────────────────────
    while True:
        try:
            query = input("🔍 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not query:
            continue
        if query.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            break

        # Embed and search
        query_embedding = model.encode(query).tolist()
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=TOP_K,
            include=["documents", "metadatas", "distances"],
        )

        documents = results["documents"][0]
        metadatas = results["metadatas"][0]
        distances = results["distances"][0]

        if not documents:
            print("\n  No results found.\n")
            continue

        # Deduplicate by book title — show at most 2 chunks per book
        seen_books = {}
        displayed = 0
        print(f"\n  Found {len(documents)} results for: \"{query}\"")

        for doc, meta, dist in zip(documents, metadatas, distances):
            book = meta.get("book_title", "")
            seen_books[book] = seen_books.get(book, 0) + 1
            if seen_books[book] > 2:
                continue
            displayed += 1
            print(format_result(displayed, doc, meta, dist))

        print()


if __name__ == "__main__":
    main()
