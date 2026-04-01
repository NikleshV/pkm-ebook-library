# PKM Ebook Library

A Personal Knowledge Management system that ingests your ebook library, builds a semantic search index, and generates structured Obsidian notes.

## Prerequisites

1. **Python 3.10+** — https://www.python.org/downloads/
2. **Calibre** (for .mobi conversion) — https://calibre-ebook.com/download_windows
   - Install to the default path: `C:\Program Files\Calibre2\`
   - Only needed if you have .mobi files
3. **Ollama** (for note generation, free & local) — https://ollama.com/download
   - After installing, pull the model: `ollama pull llama3.1:8b`

## Setup

```bash
cd N:\Projects\PKM
pip install -r requirements.txt
```

## Scripts — Run in Order

| # | Script | What it does |
|---|--------|-------------|
| 1 | `01_convert_mobi.py` | Finds all .mobi files in your library and converts them to .epub using Calibre's ebook-convert CLI. Skips files that already have a matching .epub. |
| 2 | `02_extract_text.py` | Extracts text from .pdf, .epub, and .docx files. Splits into chapters/sections and saves as .txt files with metadata.json per book. Skips non-English files. |
| 3 | `03_build_index.py` | Embeds all extracted text chunks using the all-MiniLM-L6-v2 model (local, no API) and stores them in a ChromaDB vector database for semantic search. |
| 4 | `04_chat.py` | Interactive terminal search — type a question, get the most relevant passages from your library with book/chapter attribution. |
| 5 | `05_generate_notes.py` | Generates structured Obsidian markdown notes for each book using an LLM. Uses Ollama (free, local) by default. Run separately from the pipeline. |

## Quick Start

Run scripts 01-04 in sequence using the batch file:

```bash
run_all.bat
```

Then generate Obsidian notes separately (this takes a long time):

```bash
python 05_generate_notes.py
```

## 05_generate_notes.py — Options

```bash
# Default: Ollama local, free (make sure Ollama is running)
python 05_generate_notes.py

# Use a different Ollama model
python 05_generate_notes.py --model qwen3-coder:30b

# Use Claude API instead (paid, higher quality)
python 05_generate_notes.py --claude
```

### Setting ANTHROPIC_API_KEY (only if using --claude)

**Command Prompt:**
```cmd
set ANTHROPIC_API_KEY=sk-ant-your-key-here
python 05_generate_notes.py --claude
```

**PowerShell:**
```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-your-key-here"
python 05_generate_notes.py --claude
```

**Permanent (System Environment Variable):**
1. Press Win+R, type `sysdm.cpl`, press Enter
2. Advanced tab > Environment Variables
3. Under User variables, click New
4. Variable name: `ANTHROPIC_API_KEY`
5. Variable value: `sk-ant-your-key-here`

## Estimated Run Times (for ~1,800 books)

| Script | Time |
|--------|------|
| 01_convert_mobi.py | 5-30 min (most skipped if epubs exist) |
| 02_extract_text.py | 30-90 min |
| 03_build_index.py | 15-30 min |
| 04_chat.py | Instant (interactive) |
| 05_generate_notes.py (Ollama) | 15-30 hours on CPU, 8-15 hours with GPU |
| 05_generate_notes.py (Claude) | 2-3 hours |

## Directory Structure

```
N:\Projects\PKM\
  requirements.txt          # Python dependencies
  01_convert_mobi.py        # Mobi -> EPUB converter
  02_extract_text.py        # Text extraction pipeline
  03_build_index.py         # ChromaDB index builder
  04_chat.py                # Terminal semantic search
  05_generate_notes.py      # Obsidian note generator
  run_all.bat               # Runs scripts 01-04 in sequence
  PKM-Index/
    chunks/                 # Extracted text per book (auto-generated)
    chroma/                 # ChromaDB vector database (auto-generated)
    logs/                   # Log files for each script (auto-generated)
    README.md               # This file
```

## All Scripts Are Idempotent

Every script is safe to re-run. It will skip work already done:
- 01: Skips .mobi files that already have a .epub
- 02: Skips books whose chunk folder already exists
- 03: Skips chunks already in ChromaDB
- 05: Skips books whose Obsidian note already exists

## Logs

All logs are saved to `N:\Projects\PKM\PKM-Index\logs\`:
- `convert.log` — mobi conversion results
- `extract.log` — text extraction results
- `index.log` — embedding/indexing results
- `notes.log` — Obsidian note generation results
