from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .analysis import (
    DEFAULT_VISION_MODEL,
    describe_frame,
    detect_segments,
    extract_frame,
)
from .api import serve_read_only_api
from .database import FootageDatabase
from .scanner import find_media, scan_media


DEFAULT_DATABASE = "data/footage.sqlite"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="construction-reel",
        description="Construction footage scanner",
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DATABASE,
        help=f"SQLite database path (default: {DEFAULT_DATABASE})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    project_parser = subparsers.add_parser("project", help="Manage projects")
    project_subparsers = project_parser.add_subparsers(
        dest="project_command", required=True
    )
    project_create = project_subparsers.add_parser("create")
    project_create.add_argument("name")

    scan_parser = subparsers.add_parser("scan", help="Scan and index footage")
    scan_parser.add_argument("folder")
    scan_parser.add_argument("--project", required=True)

    list_parser = subparsers.add_parser("list", help="List indexed footage")
    list_parser.add_argument("--project", required=True)
    list_parser.add_argument("--type", choices=("video", "image"))

    analyze_parser = subparsers.add_parser(
        "analyze", help="Detect scenes and add local visual tags"
    )
    analyze_parser.add_argument("--project", required=True)
    analyze_parser.add_argument("--model", default=DEFAULT_VISION_MODEL)
    analyze_parser.add_argument("--threshold", type=float, default=0.35)
    analyze_parser.add_argument("--max-segment-seconds", type=float, default=8.0)
    analyze_parser.add_argument(
        "--no-tags",
        action="store_true",
        help="Detect and save scene segments without calling the local vision model",
    )

    search_parser = subparsers.add_parser(
        "search", help="Search analyzed scene captions and visual tags"
    )
    search_parser.add_argument("query", help="Text to find in captions, tags, or filenames")
    search_parser.add_argument("--project", required=True)
    search_parser.add_argument("--limit", type=int, default=20)

    serve_parser = subparsers.add_parser(
        "serve", help="Run the local read-only search API for a future Muse connector"
    )
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    return parser


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def format_size(size: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "serve":
        api_token = os.environ.get("MUSE_REEL_API_TOKEN", "")
        if not api_token:
            raise SystemExit(
                "Set MUSE_REEL_API_TOKEN before starting the read-only API."
            )
        with FootageDatabase(args.db):
            pass
        print(f"Serving read-only search API on http://{args.host}:{args.port}")
        serve_read_only_api(
            args.db,
            api_token=api_token,
            host=args.host,
            port=args.port,
        )
        return

    with FootageDatabase(args.db) as database:
        if args.command == "project":
            if args.project_command == "create":
                project_id = database.create_project(args.name)
                print(f"Created or found project '{args.name}' (id={project_id})")
            return

        project = database.get_project(args.project)
        if project is None:
            raise SystemExit(
                f"Project '{args.project}' does not exist. "
                f"Create it with: construction-reel project create \"{args.project}\""
            )
        project_id = int(project["id"])

        if args.command == "search":
            rows = database.search_scenes(project_id, args.query, args.limit)
            if not rows:
                print("No matching analyzed scenes. Run analyze for this project first.")
                return
            for row in rows:
                tags = ", ".join(json.loads(row["tags_json"]))
                print(
                    f"{row['filename']} [{row['start_seconds']:.1f}-"
                    f"{row['end_seconds']:.1f}s] — {row['caption']}"
                )
                if tags:
                    print(f"  Tags: {tags}")
                print(f"  Preview: {row['thumbnail_path']}")
            print(f"\nMatching scenes: {len(rows)}")
            return

        if args.command == "list":
            rows = database.list_media(project_id, args.type)
            if not rows:
                print("No footage indexed.")
                return
            print(
                f"{'TYPE':<6} {'SIZE':>10} {'RESOLUTION':>12} "
                f"{'FPS':>7} {'DURATION':>10}  FILENAME"
            )
            for row in rows:
                resolution = (
                    f"{row['width']}x{row['height']}"
                    if row["width"] and row["height"]
                    else "-"
                )
                fps = f"{row['fps']:.2f}" if row["fps"] else "-"
                print(
                    f"{row['media_type']:<6} {format_size(row['file_size']):>10} "
                    f"{resolution:>12} {fps:>7} "
                    f"{format_duration(row['duration_seconds']):>10}  "
                    f"{row['filename']}"
                )
            print(f"\nIndexed media: {len(rows)}")
            return

        if args.command == "analyze":
            videos = database.list_media(project_id, "video")
            if not videos:
                print("No videos indexed for this project. Run scan first.")
                return

            completed = 0
            errors = 0
            database_path = Path(args.db).expanduser().resolve()
            preview_root = database_path.parent / "scene_previews"
            analysis_model = "scene-only" if args.no_tags else args.model
            for video in videos:
                try:
                    if database.scenes_are_current(
                        video["id"],
                        video["mtime_ns"],
                        args.threshold,
                        analysis_model,
                        args.max_segment_seconds,
                    ):
                        print(f"Unchanged {video['filename']}: scene analysis is current")
                        completed += 1
                        continue
                    segments = detect_segments(
                        video["file_path"],
                        float(video["duration_seconds"] or 0),
                        threshold=args.threshold,
                        max_segment_seconds=args.max_segment_seconds,
                    )
                    analyzed_segments = []
                    for segment_index, (start, end) in enumerate(segments, start=1):
                        preview = extract_frame(
                            video["file_path"],
                            (start + end) / 2,
                            preview_root
                            / str(video["id"])
                            / f"segment-{segment_index:04d}.jpg",
                        )
                        if args.no_tags:
                            caption, tags, model = "", [], analysis_model
                        else:
                            caption, tags = describe_frame(preview, model=args.model)
                            model = analysis_model
                        analyzed_segments.append(
                            {
                                "segment_index": segment_index,
                                "start_seconds": start,
                                "end_seconds": end,
                                "thumbnail_path": str(preview),
                                "caption": caption,
                                "tags": tags,
                                "model": model,
                                "source_mtime_ns": video["mtime_ns"],
                                "threshold": args.threshold,
                                "max_segment_seconds": args.max_segment_seconds,
                            }
                        )
                    database.replace_scene_segments(video["id"], analyzed_segments)
                    completed += 1
                    total_tags = sum(len(segment["tags"]) for segment in analyzed_segments)
                    print(
                        f"Analyzed {video['filename']}: "
                        f"{len(analyzed_segments)} segments, {total_tags} tags"
                    )
                except (OSError, RuntimeError, ValueError) as error:
                    errors += 1
                    print(f"ERROR {video['filename']}: {error}", flush=True)
            print(
                f"\nAnalysis complete: videos={len(videos)}, "
                f"completed={completed}, errors={errors}"
            )
            return

        if args.command == "scan":
            root = str(Path(args.folder).expanduser().resolve())
            traversal_errors: list[str] = []

            def report_traversal_error(error: OSError) -> None:
                message = str(error) or error.__class__.__name__
                traversal_errors.append(message)
                print(f"\nERROR accessing directory: {message}", flush=True)

            media_paths = list(find_media(root, on_error=report_traversal_error))
            scan_id = database.start_scan(project_id, root)
            indexed = 0
            updated = 0
            errors = len(traversal_errors)

            print(f"Scanning {root}")
            for index, path in enumerate(media_paths, start=1):
                try:
                    metadata = scan_media(path)
                    status, changed = database.upsert_media(project_id, metadata)
                    if status == "indexed":
                        indexed += 1
                    elif changed:
                        updated += 1
                    print(
                        f"[{index}/{len(media_paths)}] {status:<9} {path.name}",
                        end="\r",
                        flush=True,
                    )
                except (OSError, RuntimeError, ValueError) as error:
                    errors += 1
                    print(
                        f"\nERROR {path}: {error}",
                        flush=True,
                    )

            database.finish_scan(
                scan_id,
                discovered=len(media_paths),
                indexed=indexed,
                updated=updated,
                errors=errors,
            )
            print(
                f"\nScan complete: discovered={len(media_paths)}, "
                f"indexed={indexed}, updated={updated}, errors={errors}"
            )


if __name__ == "__main__":
    main()
