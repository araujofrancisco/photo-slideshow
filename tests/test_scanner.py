"""Unit tests for image discovery and natural ordering."""

import pytest

from slideshow.errors import NoImagesFound
from slideshow.scanner import find_images


def _touch(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"fake")
    return path


def test_finds_supported_and_ignores_others(tmp_path):
    _touch(tmp_path, "a.png")
    _touch(tmp_path, "b.jpg")
    _touch(tmp_path, "notes.txt")
    _touch(tmp_path, "data.csv")
    images = find_images(tmp_path)
    names = [p.name for p in images]
    assert names == ["a.png", "b.jpg"]


def test_natural_sorting(tmp_path):
    for name in ["img10.jpg", "img2.jpg", "img1.jpg"]:
        _touch(tmp_path, name)
    images = find_images(tmp_path)
    assert [p.name for p in images] == ["img1.jpg", "img2.jpg", "img10.jpg"]


def test_empty_directory_raises(tmp_path):
    with pytest.raises(NoImagesFound):
        find_images(tmp_path)


def test_nonexistent_directory_raises(tmp_path):
    with pytest.raises(NoImagesFound):
        find_images(tmp_path / "does_not_exist")
