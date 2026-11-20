# AI Construction Reel Production System

This project turns raw house-construction footage into searchable, analyzed, and
selected reels while keeping the original files untouched. Muse AI remains the
editing engine.

## Current phase

**Phase 1: footage scanning and indexing**

The working scanner recursively finds supported media, extracts metadata without
modifying the source, and stores it in SQLite.

Supported files:

- Video: `.mp4`, `.mov`, `.mkv`, `.avi`, `.m4v`
- Images: `.jpg`, `.jpeg`, `.png`

The index currently tracks projects, source media, and imported file metadata.

## macOS prerequisites

Install Homebrew and FFmpeg:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install ffmpeg
```

Optional tools for later phases:

```bash
brew install python@3.12
```

## Setup

From this repository:

```bash
cd "/Users/eesamrohith/Documents/MUSE video bot"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

Verify FFprobe:

```bash
ffprobe -version
```

## Run a scan

Create or use a project, then scan a folder:

```bash
construction-reel project create "Riverside Villa"
construction-reel scan "/absolute/path/to/footage" --project "Riverside Villa"
```

List media:

```bash
construction-reel list --project "Riverside Villa"
```

Use a custom database location:

```bash
construction-reel --db data/footage.sqlite scan "/path/to/footage" --project "Project"
```

The scanner never moves, renames, or deletes source files.

## Scan behavior

- Recursively visits the selected folder.
- Detects media by file extension.
- Uses `ffprobe` for video duration, resolution, FPS, format, and stream metadata.
- Uses Pillow for image dimensions.
- Records birth time where macOS provides it, otherwise modification time.
- Reuses source rows by canonical file path and updates metadata if a file changes.
- Stores import and update timestamps.

## Phase 2 and beyond

Later phases will add proxy generation, scene detection, segmentation, AI analysis,
search, creative generation, Muse AI export, and the local web UI. Their commands
and dependencies will be documented when implemented.

