# AI Construction Reel Production System

This project is currently a local footage scanner and indexer for house-construction
libraries. It recursively identifies supported media, records metadata in SQLite,
and keeps the original files untouched. Muse AI remains the editing engine.

Reel analysis, scene detection, AI creative direction, editing briefs, and the web
interface are planned for later phases and are not included in this version.

## Current phase

**Phase 1: footage scanning and indexing**

The working scanner recursively finds supported media, extracts metadata without
modifying the source, and stores it in SQLite.

Supported files:

- Video: `.mp4`, `.mov`, `.mkv`, `.avi`, `.m4v`
- Images: `.jpg`, `.jpeg`, `.png`

The index currently tracks projects, source media, and imported file metadata.

## macOS prerequisites

Install FFmpeg and, if needed, Python using Homebrew. Follow the official
[Homebrew installation instructions](https://brew.sh/) rather than running an
installer script directly:

```bash
brew install ffmpeg
```

Optional tools for later phases:

```bash
brew install python@3.12
```

## Setup

From a clone of this repository:

```bash
cd /path/to/muse-video-bot
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
