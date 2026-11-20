from __future__ import annotations

import argparse
from pathlib import Path

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

        if args.command == "scan":
            root = str(Path(args.folder).expanduser().resolve())
            media_paths = list(find_media(root))
            scan_id = database.start_scan(project_id, root)
            indexed = 0
            updated = 0
            errors = 0

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

