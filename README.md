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
pip install ".[dev]"
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

## Local and hosted connector API

The local API binds to localhost and requires a bearer token:

```bash
export MUSE_REEL_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
construction-reel serve
```

The read endpoints are `GET /v1/projects` and
`GET /v1/scenes/search?project=Riverside%20Villa&q=framing`. Both require
`Authorization: Bearer <MUSE_REEL_API_TOKEN>`. The unauthenticated `/health`
endpoint returns only `{"status":"ok"}`.

### Render deployment

`render.yaml` prepares a Render web service in Singapore with HTTPS, a generated
API token, a separate generated index-sync token, and a 1 GB persistent disk. It
uses Render's paid Starter service because Free web services cannot attach a
persistent disk. Check current [Render service and disk pricing](https://render.com/pricing)
before creating the service.

After deploying the Blueprint, copy the service's generated `MUSE_REEL_API_TOKEN`
and `MUSE_REEL_SYNC_TOKEN` into your local shell's environment, then sync a project:

```bash
export MUSE_REEL_API_TOKEN="<API token from Render>"
export MUSE_REEL_SYNC_TOKEN="<sync token from Render>"
export MUSE_REEL_SYNC_URL="https://<your-service>.onrender.com/v1/admin/index"
construction-reel --db data/footage.sqlite sync --project "Riverside Villa"
```

Sync uploads only project names, filenames, scene timestamps, captions, and tags.
It does not upload footage, preview frames, or local file paths. The separate
`POST /v1/admin/index` endpoint uses the sync token and must not be exposed as a Muse
tool. Muse should receive only the bearer token for the read-only project and scene
search endpoints.

A hosted endpoint is public on the internet, though the search routes require the
API token. Do not sync private project metadata unless you intend to store it on
Render and make it available to the Muse connector. Meta's connector submission
also requires business and privacy details, test access, and review; see the
official [Muse connector guidelines](https://platform.muse.ai/docs). Meeting the
guidelines does not guarantee approval.

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
