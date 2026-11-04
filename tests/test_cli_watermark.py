"""Issue #15: --watermark / --no-watermark CLI flags."""

import pytest

from r2rome.cli import build_parser


def _parse(*argv):
    return build_parser().parse_args(["render", "p.yaml", *argv])


def test_unset_defers_to_yaml():
    assert _parse().watermark is None


def test_watermark_flag():
    assert _parse("--watermark").watermark is True


def test_no_watermark_flag():
    assert _parse("--no-watermark").watermark is False


def test_flags_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        _parse("--watermark", "--no-watermark")
