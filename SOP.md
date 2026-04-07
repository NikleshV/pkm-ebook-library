# PKM Ebook Library — Standard Operating Procedure

> **Purpose**: Step-by-step guide to run, maintain, and extend the PKM ebook pipeline.
> Scripts live at `N:\Projects\PKM\`. Data lives at `N:\Projects\PKM\PKM-Index\` (local, not OneDrive).

---

## Prerequisites

### 1. Software
| Tool | Version / Notes |
|------|----------------|
| Python | 3.11+ |
| Calibre | Installed at `C:\Program Files\Calibre2\` |
| Ollama | Running at `http://localhost:11434` |
| Git | Configured with GitHub remote |

### 2. Python environment
```bash
cd N:\Projects\PKM
pip install -r requirements.txt
```

### 3. Ollama — AMD GPU setup (one-time)
The RX 6750 XT (gfx1032) requires an override to run on GPU instead of CPU:

1. Open **System Properties → Environment Variables → System Variables**
2. Add new variable:
   - Name: `HSA_OVERRIDE_GFX_VERSION`
   - Value: `10.3.0`
3. Restart Ollama: close the system tray icon, reopen it
4. Verify GPU is active:
   ```
   ollama ps
   # Should show GPU %, NOT "100% CPU"
   ```

### 4. Pull the required model (one-time)
```bash
ollama pull llama3.1:8b
```

### 5. Git — initial clone (if starting fresh)
```bash
git clone https://github.com/NikleshV/pkm-ebook-library.git N:\Projects\PKM
cd N:\Projects\PKM
git checkout dev
```

---

## Phase 1 — Ingest Pipeline (run once)

> **Shortcut**: Run `run_all.bat` from `N:\Projects\PKM\` — it executes steps 1–5 below with a pause between each.

### Step 1 — Convert .mobi to .epub
```bash
cd N:\Projects\PKM
python 01_convert_mobi.py
```
- Scans Calibre library, converts any `.mobi` that lacks a sibling `.epub`
- Safe to re-run (idempotent)
- **Check**: `PKM-Index\logs\convert.log`

### Step 2 — Extract text from all books
```bash
python 02_extract_text.py
```
- Extracts PDF (PyMuPDF), EPUB (ebooklib), DOCX (python-docx)
- Skips non-English books (filename + text sample check)
- Skips books already extracted
- Output: `PKM-Index\chunks\<title>\*.txt` + `metadata.json`
- **Check**: `PKM-Index\logs\extract.log` — look for Extracted/Skipped/Failed counts

### Step 3 — Build semantic search index
```bash
python 03_build_index.py
```
- Embeds all chunks with `all-MiniLM-L6-v2`, stores in ChromaDB
- Idempotent — skips already-indexed chunks
- **Check**: `PKM-Index\logs\index.log` — expect ~36,924 total chunks

### Step 4 — Test semantic search (optional but recommended)
```bash
python 04_chat.py
```
- Type any topic → see top matching book passages
- Type `quit` to exit

### Git commit after Phase 1
```bash
cd N:\Projects\PKM
git checkout dev
git add PKM-Index\logs\
git status   # verify no unintended files staged
git commit -m "feat: phase 1 ingest complete — 1834 books extracted, 36924 chunks indexed"
git push origin dev
```

---

## Phase 2A — Intelligence Pipeline

### Step 5 — Deduplicate books
```bash
python 05a_dedup.py
```
- Groups books by normalised title, keeps copy with most chapters
- Moves extras to `PKM-Index\chunks_dupes\`
- **Check**: `PKM-Index\logs\dedup.log` — review moved books
- Safe to re-run

```bash
git add 05a_dedup.py
git commit -m "feat: 05a_dedup — deduplicate 266 extra book copies"
git push origin dev
```

### Step 6 — Classify fiction vs non-fiction
> Requires Ollama running: check tray icon or `ollama ps`

```bash
python 05b_classify.py
# To reclassify all books:
python 05b_classify.py --reclassify
# To use a different model:
python 05b_classify.py --model llama3.2:3b
```
- Sends each book's title + 200-word excerpt to Ollama
- Saves `"genre": "fiction"` or `"nonfiction"` to each `metadata.json`
- Fast: ~1 second per book on GPU
- **Check**: `PKM-Index\logs\classify.log` — should show ~1,573 classified

```bash
git commit -m "feat: 05b_classify — 1573 books classified (573 fiction, 1000 nonfiction)"
git push origin dev
```

---

## Phase 2B — Note Generation (ongoing, run in sessions)

### Step 7 — Generate Obsidian notes (MapReduce)

> **Important**: This step takes many hours for 1,000+ books. Run in **timed sessions**.

#### First run (or after a long break)
```bash
python 05c_generate_notes.py --nonfiction-only --resume --time-limit 180
```

#### Subsequent sessions (resume where left off)
```bash
python 05c_generate_notes.py --nonfiction-only --resume --time-limit 180
```

#### Fiction only
```bash
python 05c_generate_notes.py --fiction-only --resume --time-limit 180
```

#### All books
```bash
python 05c_generate_notes.py --all --resume --time-limit 180
```

**Key flags:**
| Flag | Effect |
|------|--------|
| `--nonfiction-only` | Only process books classified as nonfiction (default) |
| `--fiction-only` | Only process books classified as fiction |
| `--all` | Process all books regardless of genre |
| `--resume` | Skip books that already have a note in the vault |
| `--time-limit N` | Stop after N minutes (checked per chapter) |
| `--claude` | Use Claude API instead of Ollama (costs money) |

**Check progress:**
- `PKM-Index\logs\notes_v2.log` — per-book results
- Obsidian vault → `PKM-Library\Books\` — new notes appearing

**After each session:**
```bash
git commit -m "feat: 05c session — generated notes for [N] books"
git push origin dev
```

### Step 8 — Create concept notes (run after each note generation session)
```bash
python 06_create_concept_notes.py
# To update existing concept notes with new books:
python 06_create_concept_notes.py --update
# Only create concepts referenced in ≥2 books:
python 06_create_concept_notes.py --min-refs 2
```
- Scans all book notes for `[[wikilinks]]`
- Creates stub notes in `PKM-Library\Concepts\`
- Activates Obsidian graph view (dead links become live backlinks)

**Check**: Open Obsidian → Graph View — concept nodes should appear

```bash
git commit -m "feat: 06 concept notes — created stubs for [N] concepts"
git push origin dev
```

---

## Release Workflow (when a stable milestone is reached)

```bash
# On GitHub: open a Pull Request from dev → main
# Review, merge, then tag the release:

git checkout main
git pull origin main
git tag -a v1.1 -m "v1.1 — MapReduce notes + concept graph"
git push origin v1.1
git checkout dev
```

---

## Routine Maintenance

### Add new books from Calibre
When new books are added to the Calibre library:

```bash
# 1. Extract new books (skips existing)
python 02_extract_text.py

# 2. Index new chunks (skips existing)
python 03_build_index.py

# 3. Re-run dedup (in case of new duplicates)
python 05a_dedup.py

# 4. Classify new books
python 05b_classify.py

# 5. Generate notes for new books
python 05c_generate_notes.py --nonfiction-only --resume --time-limit 180

# 6. Update concept notes
python 06_create_concept_notes.py --update
```

### Re-run classification with a better model
```bash
python 05b_classify.py --reclassify --model llama3.1:8b
```

### Check Ollama is using GPU
```bash
ollama ps
# Look for: llama3.1:8b   XX% GPU   (not "100% CPU")
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Ollama running at 100% CPU | AMD GPU not detected | Set `HSA_OVERRIDE_GFX_VERSION=10.3.0`, restart Ollama |
| `05b_classify.py` SyntaxError on `global` | Old code with `global OLLAMA_MODEL` | Ensure latest version from repo |
| Notes have no active wikilinks in Obsidian | Concept notes don't exist yet | Run `06_create_concept_notes.py` |
| 05c runs past time limit | Single large book spans limit | Expected — time is checked per chapter, not per book |
| ChromaDB "collection not found" | Index not built yet | Run `03_build_index.py` |
| `ollama: command not found` | Ollama not started | Open Ollama from Start Menu / system tray |
| Books not found in chat | Not yet indexed | Run `02_extract_text.py` then `03_build_index.py` |
| 05c generates very short notes | Book had only 1–2 short chapters | Normal for pamphlets/short texts |

---

## Directory Structure Reference

```
N:\Projects\PKM\
├── 01_convert_mobi.py
├── 02_extract_text.py
├── 03_build_index.py
├── 04_chat.py
├── 05_generate_notes.py       # Legacy single-pass (superseded by 05c)
├── 05a_dedup.py
├── 05b_classify.py
├── 05c_generate_notes.py      # Current: MapReduce multi-pass
├── 06_create_concept_notes.py
├── requirements.txt
├── run_all.bat
├── SOP.md                     # This file
└── PKM-Index\
    ├── chunks\                # Extracted text (gitignored)
    │   └── <book_title>\
    │       ├── chapter_001.txt
    │       └── metadata.json
    ├── chunks_dupes\          # Duplicate books (gitignored)
    ├── chroma\                # Vector index (gitignored)
    └── logs\                  # All log files (gitignored)
        ├── convert.log
        ├── extract.log
        ├── index.log
        ├── dedup.log
        ├── classify.log
        └── notes_v2.log

Obsidian Vault\
└── PKM-Library\
    ├── Books\                 # Generated book notes (05c output)
    └── Concepts\              # Concept stubs (06 output)
```

---

## Log File Quick Reference

| Log | Script | Key metrics to check |
|-----|--------|---------------------|
| `convert.log` | 01 | Converted / Skipped / Failed |
| `extract.log` | 02 | Extracted / Skipped / Failed |
| `index.log` | 03 | Added / Failed / Total in DB |
| `dedup.log` | 05a | Groups found / Books moved |
| `classify.log` | 05b | Classified / fiction / nonfiction / Skipped / Failed |
| `notes_v2.log` | 05c | Generated / Skipped / Failed / time per book |
