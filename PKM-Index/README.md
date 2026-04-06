# PKM Ebook Library

A Personal Knowledge Management system that ingests your ebook library, builds a semantic search index, and generates detailed structured Obsidian notes using a multi-pass MapReduce approach.

## Prerequisites

1. **Python 3.10+** — https://www.python.org/downloads/
2. **Calibre** (for .mobi conversion) — https://calibre-ebook.com/download_windows
   - Install to the default path: `C:\Program Files\Calibre2\`
   - Only needed if you have .mobi files
3. **Ollama** (for classification + note generation, free & local) — https://ollama.com/download
   - After installing, pull the model: `ollama pull llama3.1:8b`
   - For AMD GPUs: set `HSA_OVERRIDE_GFX_VERSION=10.3.0` environment variable

## Setup

```bash
cd N:\Projects\PKM
pip install -r requirements.txt
```

## Scripts — Run in Order

### Phase 1: Ingest Pipeline

| # | Script | What it does |
|---|--------|-------------|
| 1 | `01_convert_mobi.py` | Converts .mobi files to .epub using Calibre. Skips files that already have a matching .epub. |
| 2 | `02_extract_text.py` | Extracts text from .pdf, .epub, .docx. Splits by chapter/section, saves as .txt + metadata.json. Skips non-English files. |
| 3 | `03_build_index.py` | Embeds all text chunks with all-MiniLM-L6-v2 (local) and stores in ChromaDB for semantic search. |
| 4 | `04_chat.py` | Interactive terminal search — type a question, get top relevant passages with book/chapter attribution. |

### Phase 2: Note Generation Pipeline

| # | Script | What it does |
|---|--------|-------------|
| 5a | `05a_dedup.py` | Deduplicates books by normalised title. Keeps the copy with the most chapters, moves extras to `chunks_dupes/`. |
| 5b | `05b_classify.py` | Classifies each book as fiction or non-fiction using Ollama. Saves genre into metadata.json. |
| 5c | `05c_generate_notes.py` | **Multi-pass MapReduce** note generation. Reads the ENTIRE book, not a truncated sample. Produces detailed student-level Obsidian notes. |

### Legacy (replaced by 05a-05c)
| | `05_generate_notes.py` | Original single-pass note generator. Kept for reference. Use 05c instead. |

## Quick Start

Run the ingest pipeline (scripts 01-04):

```bash
run_all.bat
```

Then run the note generation pipeline:

```bash
python 05a_dedup.py
python 05b_classify.py
python 05c_generate_notes.py
```

## 05c_generate_notes.py — How It Works

Unlike the original single-pass approach (which truncated books to 6,000 words), this uses a **MapReduce** pattern:

1. **Pass 1 (Map)**: Each chapter is sent individually to the LLM with its full text (~2,500 words per call). The LLM generates detailed bulleted notes per chapter.
2. **Pass 2 (Reduce)**: All chapter notes (now ~5x compressed) are sent to a synthesis call that generates the final structured Obsidian note with cross-chapter themes, within-book linkages, and concept network.

This means the LLM reads the **entire book**, not a truncated sample.

### Options

```bash
# Default: non-fiction only, 3-hour time limit, Ollama
python 05c_generate_notes.py

# Process all genres
python 05c_generate_notes.py --all

# Fiction books only
python 05c_generate_notes.py --fiction-only

# Custom time limit (2 hours)
python 05c_generate_notes.py --time-limit 120

# Re-process books that already have notes
python 05c_generate_notes.py --resume

# Use Claude API (paid, higher quality)
python 05c_generate_notes.py --claude

# Different Ollama model
python 05c_generate_notes.py --model qwen3-coder:30b
```

### Setting ANTHROPIC_API_KEY (only if using --claude)

**Command Prompt:**
```cmd
set ANTHROPIC_API_KEY=sk-ant-your-key-here
python 05c_generate_notes.py --claude
```

**PowerShell:**
```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-your-key-here"
python 05c_generate_notes.py --claude
```

**Permanent (System Environment Variable):**
1. Press Win+R, type `sysdm.cpl`, press Enter
2. Advanced tab > Environment Variables
3. Under User variables, click New
4. Variable name: `ANTHROPIC_API_KEY`
5. Variable value: `sk-ant-your-key-here`

## Estimated Run Times (for ~1,568 unique books after dedup)

| Script | Time |
|--------|------|
| 01_convert_mobi.py | 5-30 min (most skipped if epubs exist) |
| 02_extract_text.py | 30-90 min |
| 03_build_index.py | 15-30 min |
| 04_chat.py | Instant (interactive) |
| 05a_dedup.py | Under 1 min |
| 05b_classify.py | 1-2 hours (GPU) |
| 05c (Ollama, GPU) | ~5-15 min per book depending on chapter count. Run in 3-hour sessions. |
| 05c (Claude API) | ~2-3 min per book. Much faster, costs ~$45-60 total. |

## Directory Structure

```
N:\Projects\PKM\
  requirements.txt            # Python dependencies
  01_convert_mobi.py          # Mobi -> EPUB converter
  02_extract_text.py          # Text extraction pipeline
  03_build_index.py           # ChromaDB index builder
  04_chat.py                  # Terminal semantic search
  05_generate_notes.py        # [Legacy] Single-pass note generator
  05a_dedup.py                # Deduplicate extracted books
  05b_classify.py             # Fiction/non-fiction classifier
  05c_generate_notes.py       # Multi-pass MapReduce note generator
  run_all.bat                 # Runs ingest pipeline (01-04)
  PKM-Index/
    chunks/                   # Extracted text per book (auto-generated)
    chunks_dupes/             # Duplicate books moved here by 05a
    chroma/                   # ChromaDB vector database (auto-generated)
    logs/                     # Log files for each script (auto-generated)
    README.md                 # This file
```

## Output: Obsidian Notes

Notes are saved to:
```
Obsidian Vault/PKM-Library/Books/<Book Title>.md
```

Each note includes:
- YAML frontmatter (title, author, year, tags, genre)
- Overview (4-5 sentences)
- Chapter-by-chapter detailed notes (5-8 bullets each)
- Cross-chapter themes with chapter references
- Key frameworks and models with [[wikilinks]]
- Within-book linkages (how ideas build on each other)
- Key evidence and data
- Notable quotes with chapter references
- Connections to other works
- Open questions
- Personal action items

## All Scripts Are Idempotent

Every script is safe to re-run. It will skip work already done:
- 01: Skips .mobi files that already have a .epub
- 02: Skips books whose chunk folder already exists
- 03: Skips chunks already in ChromaDB
- 05a: One-time dedup (re-run is harmless)
- 05b: Skips books already classified (use --reclassify to redo)
- 05c: Skips books with existing notes (use --resume to redo)

## Logs

All logs are saved to `N:\Projects\PKM\PKM-Index\logs\`:
- `convert.log` — mobi conversion
- `extract.log` — text extraction
- `index.log` — embedding/indexing
- `dedup.log` — deduplication
- `classify.log` — fiction/non-fiction classification
- `notes_v2.log` — multi-pass note generation
