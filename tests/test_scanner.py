from PIL import Image

from muse_reel.scanner import find_media, scan_media


def test_finds_supported_media_recursively(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "clip.mp4").write_bytes(b"")
    (tmp_path / "nested" / "photo.png").touch()
    (tmp_path / "nested" / "ignored.txt").touch()

    found = {path.name for path in find_media(tmp_path)}
    assert found == {"clip.mp4", "photo.png"}


def test_scans_image_metadata(tmp_path):
    image_path = tmp_path / "frame.jpg"
    Image.new("RGB", (1920, 1080), color=(30, 60, 90)).save(image_path)

    metadata = scan_media(image_path)

    assert metadata.filename == "frame.jpg"
    assert metadata.media_type == "image"
    assert metadata.width == 1920
    assert metadata.height == 1080
    assert metadata.duration_seconds is None
    assert metadata.fps is None
    assert metadata.file_format == "JPG"
