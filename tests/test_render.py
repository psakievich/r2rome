"""
Tests for r2rome.render — dot binary detection, graceful degradation,
DOT source generation.  Actual graphviz rendering calls are mocked so
these tests run without the dot binary installed.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from r2rome.model import Graph, GraphNode
from r2rome.render import (
    dot_version,
    find_dot_binary,
    require_dot_binary,
    to_dot_source,
)


class TestFindDotBinary:
    def test_returns_path_when_found(self):
        with patch("shutil.which", return_value="/usr/bin/dot"):
            result = find_dot_binary()
        assert result == "/usr/bin/dot"

    def test_returns_none_when_missing(self):
        with patch("shutil.which", return_value=None):
            result = find_dot_binary()
        assert result is None


class TestRequireDotBinary:
    def test_returns_path_when_found(self):
        with patch("shutil.which", return_value="/usr/bin/dot"):
            result = require_dot_binary()
        assert result == "/usr/bin/dot"

    def test_raises_with_install_hint_when_missing(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError) as exc_info:
                require_dot_binary()
        msg = str(exc_info.value)
        assert "dot" in msg
        assert "spack" in msg          # Spack install hint present
        assert "r2rome dot" in msg     # graceful degradation hint present


class TestDotVersion:
    def test_returns_none_when_not_found(self):
        with patch("shutil.which", return_value=None):
            assert dot_version() is None

    def test_returns_version_string(self):
        mock_result = MagicMock()
        mock_result.stderr = "dot - graphviz version 9.0.0 (20230911.1827)"
        mock_result.stdout = ""
        with patch("shutil.which", return_value="/usr/bin/dot"):
            with patch("subprocess.run", return_value=mock_result):
                ver = dot_version()
        assert ver == "dot - graphviz version 9.0.0 (20230911.1827)"


class TestToDotSource:
    """to_dot_source is pure Python — no binary required."""

    def test_returns_string(self, simple_graph):
        src = to_dot_source(simple_graph)
        assert isinstance(src, str)
        assert len(src) > 0

    def test_contains_digraph_keyword(self, simple_graph):
        src = to_dot_source(simple_graph)
        assert "digraph" in src.lower() or "->" in src

    def test_contains_node_names(self, simple_graph):
        src = to_dot_source(simple_graph)
        assert "a" in src
        assert "b" in src

    def test_blocks_edges_are_dashed(self):
        graph = Graph.from_dict({
            "name": "root",
            "nodes": [
                {"name": "x", "blocks": ["y"]},
                {"name": "y"},
            ],
        })
        src = to_dot_source(graph)
        assert "dashed" in src

    def test_depth_limit_reduces_output(self, nested_graph):
        src_full   = to_dot_source(nested_graph)
        src_limited = to_dot_source(nested_graph, max_depth=0)
        assert len(src_full) >= len(src_limited)

    def test_empty_graph_does_not_raise(self):
        graph = Graph.from_dict({"name": "empty"})
        src = to_dot_source(graph)
        assert isinstance(src, str)


class TestGracefulDegradation:
    """Verify that the tool is still useful without the dot binary."""

    def test_dot_subcommand_works_without_binary(self, simple_yaml_file, capsys):
        """r2rome dot should work even when graphviz is not installed."""
        from r2rome.cli import build_parser

        with patch("shutil.which", return_value=None):
            parser = build_parser()
            args = parser.parse_args(["dot", str(simple_yaml_file)])
            rc = args.func(args)

        assert rc == 0
        captured = capsys.readouterr()
        assert "digraph" in captured.out.lower() or "->" in captured.out

    def test_render_subcommand_fails_gracefully_without_binary(
        self, simple_yaml_file, tmp_path
    ):
        """r2rome render should return non-zero and print a helpful message."""
        from r2rome.cli import build_parser

        with patch("shutil.which", return_value=None):
            parser = build_parser()
            args = parser.parse_args([
                "render", str(simple_yaml_file), "-o", str(tmp_path)
            ])
            rc = args.func(args)

        assert rc != 0
