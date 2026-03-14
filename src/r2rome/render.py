"""
r2rome.render
~~~~~~~~~~~~~
Handles rendering Graph objects to SVG/PNG/PDF via the graphviz dot binary.

Graphviz (the dot binary) is an optional runtime dependency.  If it is not
found on PATH, r2rome degrades gracefully:

  - 'dot' subcommand still works (pure Python, no binary needed)
  - 'render' subcommand emits a clear error with install instructions
    rather than a confusing import traceback

Install hint shown when dot is missing:
  System package managers:
    apt install graphviz
    brew install graphviz
    dnf install graphviz
  Via Spack:
    spack install graphviz
    spack load graphviz
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional


from r2rome.model import Graph
from r2rome.assemble import build_digraph


# ---------------------------------------------------------------------------
# Graphviz binary detection
# ---------------------------------------------------------------------------

def find_dot_binary() -> Optional[str]:
    """Return the path to the dot binary, or None if not found."""
    return shutil.which("dot")


def require_dot_binary() -> str:
    """Return the dot binary path, raising a clear error if missing.

    Raises:
        RuntimeError: with install instructions for common platforms.
    """
    dot = find_dot_binary()
    if dot is None:
        raise RuntimeError(
            "The 'dot' binary (Graphviz) was not found on PATH.\n\n"
            "Install Graphviz to enable rendering:\n"
            "  apt:   sudo apt install graphviz\n"
            "  brew:  brew install graphviz\n"
            "  dnf:   sudo dnf install graphviz\n"
            "  spack: spack install graphviz && spack load graphviz\n\n"
            "r2rome can still generate .dot files without Graphviz installed.\n"
            "Use:  r2rome dot <file.yaml>"
        )
    return dot


def dot_version() -> Optional[str]:
    """Return the installed Graphviz version string, or None."""
    dot = find_dot_binary()
    if dot is None:
        return None
    try:
        result = subprocess.run(
            [dot, "-V"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # dot -V writes to stderr
        return (result.stderr or result.stdout).strip()
    except (subprocess.TimeoutExpired, OSError):
        return None


# ---------------------------------------------------------------------------
# DOT source generation (no binary required)
# ---------------------------------------------------------------------------

def to_dot_source(
    graph: Graph,
    max_depth: Optional[int] = None,
    output_dir: Optional[str] = None,
) -> str:
    """Return the DOT language source for a graph.

    This is pure Python — no dot binary required.

    Args:
        graph:      The Graph to convert.
        max_depth:  Depth limit for subgraph expansion (None = unlimited).
        output_dir: Used to generate href paths on collapsed nodes.

    Returns:
        A DOT language string suitable for piping to dot or saving as .gv.
    """
    digraph = build_digraph(graph, max_depth=max_depth, output_dir=output_dir)
    return digraph.source


# ---------------------------------------------------------------------------
# Rendering to image formats
# ---------------------------------------------------------------------------

def render_graph(
    graph: Graph,
    output_path: Path,
    fmt: str = "svg",
    max_depth: Optional[int] = None,
    cleanup: bool = True,
) -> Path:
    """Render a graph to an image file using the dot binary.

    Args:
        graph:       The Graph to render.
        output_path: Destination file path (without extension — graphviz adds it).
        fmt:         Output format: 'svg', 'png', 'pdf'. Default 'svg'.
        max_depth:   Collapse subgraphs beyond this depth to hyperlinked nodes.
        cleanup:     Remove the intermediate .dot file after rendering.

    Returns:
        Path to the rendered output file (output_path with format extension).

    Raises:
        RuntimeError: If dot binary is not found.
    """
    require_dot_binary()

    digraph = build_digraph(
        graph,
        max_depth=max_depth,
        output_dir=str(output_path.parent),
    )

    # graphviz library renders and optionally cleans up the intermediate source
    rendered = digraph.render(
        filename=str(output_path),
        format=fmt,
        cleanup=cleanup,
    )
    return Path(rendered)


def render_all_levels(
    root: Graph,
    output_dir: Path,
    fmt: str = "svg",
    max_depth: Optional[int] = None,
    cleanup: bool = True,
) -> List[Path]:
    """Render each graph level to a separate file.

    The root graph is rendered as 'index.<fmt>'.  Each subgraph is rendered
    as '<name>.<fmt>', recursively.  Nodes that have a corresponding subgraph
    file are given an href so SVG output is navigable in a browser.

    Args:
        root:       Root Graph to start from.
        output_dir: Directory to write rendered files into.
        fmt:        Output format.
        max_depth:  Passed through to each render call.
        cleanup:    Remove intermediate .dot files.

    Returns:
        List of Paths to all rendered files.
    """
    require_dot_binary()
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered: List[Path] = []

    def _render_recursive(graph: Graph, depth: int) -> None:
        name = "index" if graph.name == "root" else graph.name
        out  = output_dir / name
        path = render_graph(
            graph,
            output_path=out,
            fmt=fmt,
            max_depth=max_depth,
            cleanup=cleanup,
        )
        rendered.append(path)

        for subgraph in graph.subgraphs:
            _render_recursive(subgraph, depth + 1)

        # Also recurse into node children (inline child graphs)
        for node in graph.nodes:
            if node.children is not None:
                _render_recursive(node.children, depth + 1)

    _render_recursive(root, 0)
    return rendered
