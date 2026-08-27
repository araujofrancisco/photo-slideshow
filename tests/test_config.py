"""Unit tests for configuration loading and precedence."""

import os

import pytest

from slideshow.config import Config, load_config
from slideshow.errors import ConfigError


class _Args:
    """Minimal stand-in for argparse.Namespace."""

    def __init__(self, **kwargs):
        self.input_dir = None
        self.output = None
        self.delay = None
        self.transition = None
        self.crossfade = None
        self.resolution = None
        self.overwrite = False
        self.ken_burns = False
        self.encoder = None
        self.env_file = ".env"
        for key, value in kwargs.items():
            setattr(self, key, value)


@pytest.fixture
def isolated_env(monkeypatch):
    """Make env loading deterministic: only monkeypatched os.environ applies.

    python-dotenv would otherwise fall back to find_dotenv() and pick up a
    real .env from the tree, which breaks value isolation in these tests.
    """
    monkeypatch.setattr(os, "environ", {})
    monkeypatch.setattr("slideshow.config.load_dotenv", lambda *a, **k: False)


def test_defaults_applied(isolated_env):
    cfg = load_config(_Args(input_dir="/in", output="/out.mp4"))
    assert cfg.delay_seconds == 5.0
    assert cfg.transition == "cut"
    assert cfg.crossfade_seconds == 1.0
    assert (cfg.width, cfg.height) == (1920, 1080)
    assert cfg.overwrite is False


def test_cli_overrides_env(isolated_env):
    os.environ["INPUT_DIR"] = "/env_in"
    os.environ["OUTPUT_FILE"] = "/env_out.mp4"
    os.environ["DELAY_SECONDS"] = "2"
    cfg = load_config(_Args(input_dir="/cli_in", delay=10.0))
    assert str(cfg.input_dir) == "/cli_in"
    assert cfg.delay_seconds == 10.0


def test_env_used_when_cli_absent(isolated_env):
    os.environ["INPUT_DIR"] = "/env_in"
    os.environ["OUTPUT_FILE"] = "/env_out.mp4"
    os.environ["TRANSITION"] = "crossfade"
    os.environ["OVERWRITE"] = "true"
    cfg = load_config(_Args())
    assert str(cfg.input_dir) == "/env_in"
    assert cfg.transition == "crossfade"
    assert cfg.overwrite is True


def test_resolution_parsing(isolated_env):
    cfg = load_config(_Args(input_dir="/in", output="/o.mp4", resolution="1280x720"))
    assert (cfg.width, cfg.height) == (1280, 720)


def test_missing_required_raises(isolated_env):
    with pytest.raises(ConfigError):
        load_config(_Args(output="/o.mp4"))
    with pytest.raises(ConfigError):
        load_config(_Args(input_dir="/in"))


def test_validation_crossfade_less_than_delay():
    cfg = Config(
        input_dir=__import__("pathlib").Path("/in"),
        output_file=__import__("pathlib").Path("/o.mp4"),
        delay_seconds=2.0,
        transition="crossfade",
        crossfade_seconds=2.0,  # invalid: must be < delay
        width=1920,
        height=1080,
        overwrite=False,
        ken_burns=False,
        encoder="libx264",
    )
    with pytest.raises(ConfigError):
        cfg.validate()


def test_validation_even_resolution():
    cfg = Config(
        input_dir=__import__("pathlib").Path("/in"),
        output_file=__import__("pathlib").Path("/o.mp4"),
        delay_seconds=5.0,
        transition="cut",
        crossfade_seconds=1.0,
        width=1921,
        height=1080,
        overwrite=False,
        ken_burns=False,
        encoder="libx264",
    )
    with pytest.raises(ConfigError):
        cfg.validate()
