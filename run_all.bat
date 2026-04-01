@echo off
title PKM Ebook Library — Ingest Pipeline
echo ================================================================
echo   PKM Ebook Library — Ingest Pipeline
echo   This will run scripts 01 through 04 in sequence.
echo   Script 05 (note generation) is NOT included — run it separately.
echo ================================================================
echo.

REM ── Step 1: Convert .mobi to .epub ──────────────────────────────
echo [Step 1/4] Converting .mobi files to .epub ...
echo ----------------------------------------------------------------
python "%~dp0\01_convert_mobi.py"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo WARNING: Step 1 exited with errors. Check logs\convert.log
)
echo.
echo Step 1 complete. Press any key to continue to Step 2...
pause >nul
echo.

REM ── Step 2: Extract text ────────────────────────────────────────
echo [Step 2/4] Extracting text from books ...
echo ----------------------------------------------------------------
python "%~dp0\02_extract_text.py"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo WARNING: Step 2 exited with errors. Check logs\extract.log
)
echo.
echo Step 2 complete. Press any key to continue to Step 3...
pause >nul
echo.

REM ── Step 3: Build search index ─────────────────────────────────
echo [Step 3/4] Building ChromaDB search index ...
echo ----------------------------------------------------------------
python "%~dp0\03_build_index.py"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo WARNING: Step 3 exited with errors. Check logs\index.log
)
echo.
echo Step 3 complete. Press any key to continue to Step 4...
pause >nul
echo.

REM ── Step 4: Launch search chat ─────────────────────────────────
echo [Step 4/4] Launching semantic search chat ...
echo ----------------------------------------------------------------
echo   Type a question to search your library. Type 'quit' to exit.
echo.
python "%~dp0\04_chat.py"

echo.
echo ================================================================
echo   Pipeline complete!
echo.
echo   To generate Obsidian notes, run separately:
echo     python 05_generate_notes.py
echo ================================================================
pause
