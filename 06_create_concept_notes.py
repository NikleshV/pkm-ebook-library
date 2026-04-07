"""
06_create_concept_notes.py — Create stub concept notes for every [[wikilink]]
found across all generated Obsidian book notes.

Scans PKM-Library/Books/*.md, extracts all [[wikilink]] terms, and creates
a stub note in PKM-Library/Concepts/<Term>.md for each unique term.
Each stub lists the books that reference it (backlinks), lighting up
Obsidian's graph view and making all wikilinks active.

Idempotent: skips concepts that already have a note (unless --update).
--update: re-writes existing concept notes to add newly discovered books.
"""

import argparse
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────
OBSIDIAN_VAULT = Path(
    r"C:\Users\Niklesh\OneDrive\Documents\MD Obsidian\Niklesh Notes_Main"
)
BOOKS_DIR = OBSIDIAN_VAULT / "PKM-Library" / "Books"
CONCEPTS_DIR = OBSIDIAN_VAULT / "PKM-Library" / "Concepts"

# Minimum number of books that must reference a term to create a concept note.
# Filters out hyper-specific one-off terms.
MIN_REFERENCES = 1

# Wikilinks shorter than this (chars) are skipped — too generic
MIN_TERM_LENGTH = 3

# Terms that are too generic to be useful concept notes
SKIP_TERMS = {
    "wikilink", "link", "note", "chapter", "book", "author",
    "introduction", "conclusion", "summary", "overview",
}


def sanitise_filename(name: str) -> str:
    """Convert a concept term into a safe Obsidian filename."""
    name = unicodedata.normalize("NFKD", name)
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:100]


def extract_wikilinks(text: str) -> set[str]:
    """Extract all [[wikilink]] terms from markdown text."""
    raw = re.findall(r'\[\[([^\]|#]+?)(?:\|[^\]]*)?\]\]', text)
    terms = set()
    for term in raw:
        term = term.strip()
        if (len(term) >= MIN_TERM_LENGTH
                and term.lower() not in SKIP_TERMS
                and not term.startswith("http")):
            terms.add(term)
    return terms


def get_book_title(note_path: Path) -> str:
    """Extract title from YAML frontmatter, fallback to filename."""
    try:
        text = note_path.read_text(encoding="utf-8")
        m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', text, re.MULTILINE)
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return note_path.stem


def make_concept_note(term: str, book_refs: list[dict]) -> str:
    """Generate a stub concept note."""
    book_list = "\n".join(
        f"- [[{ref['title']}]]" for ref in sorted(book_refs, key=lambda r: r["title"])
    )

    return f"""---
concept: "{term}"
tags: [concept, pkm-auto-generated]
book_count: {len(book_refs)}
---

# {term}

> *Auto-generated concept note. Edit to add your own definition and notes.*

## Definition
*(Add your own definition here)*

## Referenced In
{book_list}

## Notes
*(Add your own notes, connections, and insights here)*
"""


def main():
    parser = argparse.ArgumentParser(
        description="Create Obsidian concept stub notes from [[wikilinks]]."
    )
    parser.add_argument(
        "--update", action="store_true",
        help="Re-write existing concept notes to include newly discovered books"
    )
    parser.add_argument(
        "--min-refs", type=int, default=MIN_REFERENCES,
        help=f"Minimum book references to create a concept note (default: {MIN_REFERENCES})"
    )
    args = parser.parse_args()

    CONCEPTS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Scan all book notes for wikilinks ─────────────────────────────
    if not BOOKS_DIR.exists():
        print(f"ERROR: Books directory not found: {BOOKS_DIR}")
        return

    book_notes = list(BOOKS_DIR.glob("*.md"))
    print(f"Scanning {len(book_notes)} book notes for [[wikilinks]]...")

    # concept_term → list of {title, note_path}
    concept_map: dict[str, list[dict]] = defaultdict(list)

    for note_path in book_notes:
        try:
            text = note_path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"  WARN: Could not read {note_path.name}: {e}")
            continue

        title = get_book_title(note_path)
        links = extract_wikilinks(text)

        for term in links:
            concept_map[term].append({"title": title, "path": note_path})

    print(f"Found {len(concept_map)} unique concepts across all notes.")

    # ── Filter by minimum references ──────────────────────────────────
    filtered = {
        term: refs for term, refs in concept_map.items()
        if len(refs) >= args.min_refs
    }
    print(f"Creating notes for {len(filtered)} concepts "
          f"(referenced in ≥{args.min_refs} book(s))...")

    # ── Create/update concept notes ───────────────────────────────────
    created = 0
    updated = 0
    skipped = 0

    for term, refs in sorted(filtered.items()):
        safe_name = sanitise_filename(term)
        concept_path = CONCEPTS_DIR / f"{safe_name}.md"

        if concept_path.exists() and not args.update:
            skipped += 1
            continue

        note_content = make_concept_note(term, refs)

        try:
            concept_path.write_text(note_content, encoding="utf-8")
            if concept_path.exists() and args.update:
                updated += 1
            else:
                created += 1
        except Exception as e:
            print(f"  FAIL: {term}: {e}")

    print(f"\nDone! Created: {created} | Updated: {updated} | Skipped: {skipped}")

    # ── Stats ──────────────────────────────────────────────────────────
    by_ref_count = sorted(concept_map.items(), key=lambda x: len(x[1]), reverse=True)
    print(f"\nTop 20 most-referenced concepts:")
    for term, refs in by_ref_count[:20]:
        print(f"  {len(refs):3d}x  {term}")


if __name__ == "__main__":
    main()
