@echo off
title PKM Ebook Library — Full Pipeline
echo ================================================================
echo   PKM Ebook Library — Full Pipeline
echo   Phase 1: Ingest (scripts 01-04)
echo   Phase 2: Dedup + Classify (05a, 05b)
echo   Note generation (05c) is run separately — it takes hours.
echo ================================================================
echo.

REM ── Step 1: Convert .mobi to .epub ──────────────────────────────
echo [Step 1/6] Converting .mobi files to .epub ...
echo ----------------------------------------------------------------
python "%~dp0\01_convert_mobi.py"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo WARNING: Step 1 exited with errors. Check logs\convert.log
)
echo.
echo Step 1 complete. Press any key to continue...
pause >nul
echo.

REM ── Step 2: Extract text ────────────────────────────────────────
echo [Step 2/6] Extracting text from books ...
echo ----------------------------------------------------------------
python "%~dp0\02_extract_text.py"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo WARNING: Step 2 exited with errors. Check logs\extract.log
)
echo.
echo Step 2 complete. Press any key to continue...
pause >nul
echo.

REM ── Step 3: Build search index ─────────────────────────────────
echo [Step 3/6] Building ChromaDB search index ...
echo ----------------------------------------------------------------
python "%~dp0\03_build_index.py"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo WARNING: Step 3 exited with errors. Check logs\index.log
)
echo.
echo Step 3 complete. Press any key to continue...
pause >nul
echo.

REM ── Step 4: Deduplicate ────────────────────────────────────────
echo [Step 4/6] Deduplicating extracted books ...
echo ----------------------------------------------------------------
python "%~dp0\05a_dedup.py"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo WARNING: Step 4 exited with errors. Check logs\dedup.log
)
echo.
echo Step 4 complete. Press any key to continue...
pause >nul
echo.

REM ── Step 5: Classify fiction/non-fiction ────────────────────────
echo [Step 5/6] Classifying books (fiction vs non-fiction) ...
echo ----------------------------------------------------------------
echo   This requires Ollama to be running (ollama serve).
echo.
python "%~dp0\05b_classify.py"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo WARNING: Step 5 exited with errors. Check logs\classify.log
)
echo.
echo Step 5 complete. Press any key to continue...
pause >nul
echo.

REM ── Step 6: Launch search chat ─────────────────────────────────
echo [Step 6/6] Launching semantic search chat ...
echo ----------------------------------------------------------------
echo   Type a question to search your library. Type 'quit' to exit.
echo.
python "%~dp0\04_chat.py"

echo.
echo ================================================================
echo   Pipeline complete!
echo.
echo   To generate Obsidian notes, run separately:
echo     python 05c_generate_notes.py
echo.
echo   This uses multi-pass MapReduce (reads entire books).
echo   Default: non-fiction only, 3-hour time limit.
echo   Run multiple sessions to cover all books.
echo ================================================================
pause
