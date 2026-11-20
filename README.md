# AI Construction Reel Production System

This project is a local footage scanner and scene-search tool for house-construction
libraries. It records metadata, detects scene changes, and creates visual captions
and tags on the local machine. Meta Muse remains a separate editing destination.

The tool does not edit, move, rename, or delete source footage. The optional Muse
connector API is a local read-only prototype; it is not listed in or connected to
Meta Muse.

## Current phase

**Phase 2: scanning, scene segmentation, and local visual tags**

The scanner recursively finds supported media, extracts metadata without changing
the source, and stores it in SQLite. Video analysis detects scene cuts, splits long
shots into shorter segments, extracts small preview frames, and tags those frames
with a local Ollama vision model.

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

Install Ollama for local visual tagging. Then download the 3.2 GB vision model once:

```bash
ollama pull qwen2.5vl:3b
```

The footage frames are sent only to the Ollama service on this Mac. For scene
segmentation without visual captions or tags, use `analyze --no-tags`.
Captions and tags are model suggestions; review them before using them to choose
shots.

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

Analyze indexed videos and search their segments:

```bash
construction-reel analyze --project "Riverside Villa"
construction-reel search "wall framing" --project "Riverside Villa"
```

Re-running `analyze` skips videos whose file timestamp, scene threshold, and model
match the saved analysis. Preview frames are stored beside the database in
`scene_previews/`; the original videos stay where they are.

Use a custom database location:

```bash
construction-reel --db data/footage.sqlite scan "/path/to/footage" --project "Project"
```

The scanner never moves, renames, or deletes source files.

## Local connector prototype

The read-only API lets a future Muse connector search indexed scene captions and
tags. It binds to localhost only, returns metadata without local file paths or image
bytes, and requires a bearer token:

```bash
export MUSE_REEL_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
construction-reel serve
```

Available endpoints are `GET /v1/projects` and
`GET /v1/scenes/search?project=Riverside%20Villa&q=framing`. Send the token as
`Authorization: Bearer <token>`.

This local server cannot be connected directly from Meta Muse. A Muse listing needs
a hosted HTTPS endpoint, authentication and privacy documentation, business
verification, test access, and Meta review. See the official
[Muse connector guidelines](https://platform.muse.ai/docs). Do not expose the local
API to the public internet as-is.

## Scan behavior

- Recursively visits the selected folder.
- Detects media by file extension.
- Uses `ffprobe` for video duration, resolution, FPS, format, and stream metadata.
- Uses Pillow for image dimensions.
- Records birth time where macOS provides it, otherwise modification time.
- Reuses source rows by canonical file path and updates metadata if a file changes.
- Stores import and update timestamps.

## Later phases

Later phases may add proxy generation, creative direction, editing briefs, Muse
connector deployment, and the local web UI.
