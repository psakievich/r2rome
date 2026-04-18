"""
r2rome.cli
~~~~~~~~~~
Command-line interface.

Usage
-----
  r2rome render project.yaml -o out/          # SVG + HTML, fully offline
  r2rome render project.yaml -o out/ --cdn    # interactive CDN viewer
  r2rome render project.yaml -o out/ --fmt png
  r2rome render project.yaml -o out/ --depth 2

  r2rome dot project.yaml                     # DOT source to stdout
  r2rome dot project.yaml --level toolchain   # single subgraph to stdout
  r2rome dot project.yaml -o graph.gv         # DOT source to file

  r2rome info project.yaml                    # summary of graph structure

  r2rome init                                 # print starter template to stdout
  r2rome init project.yaml                    # write starter template to file
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from r2rome import __version__
from r2rome.model import load, Graph, build_node_registry
from r2rome.render import (
    dot_version,
    find_dot_binary,
    render_all_levels,
    to_dot_source,
)
from r2rome.html_writer import write_all_pages, graph_to_json_data

# argcomplete is an optional dependency — graceful degradation when absent
try:
    import argcomplete
    from argcomplete.completers import FilesCompleter
    _HAS_ARGCOMPLETE = True
except ImportError:
    _HAS_ARGCOMPLETE = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_levels(graph: Graph, parent_name: Optional[str] = None) -> List[dict]:
    """Recursively collect level descriptors for html_writer."""
    levels = [{
        "name":     graph.name,
        "title":    graph.title or graph.name,
        "parent":   parent_name,
        "children": [sg.name for sg in graph.subgraphs] +
                    [n.name for n in graph.nodes if n.children],
    }]
    for sg in graph.subgraphs:
        levels.extend(_collect_levels(sg, parent_name=graph.name))
    for node in graph.nodes:
        if node.children:
            levels.extend(_collect_levels(node.children, parent_name=graph.name))
    return levels


def _find_subgraph_by_name(graph: Graph, name: str) -> Optional[Graph]:
    """Breadth-first search for a named subgraph."""
    queue = [graph]
    while queue:
        current = queue.pop(0)
        if current.name == name:
            return current
        queue.extend(current.subgraphs)
        for node in current.nodes:
            if node.children:
                queue.append(node.children)
    return None


def _check_dot(warn_only: bool = False) -> None:
    """Print a warning or note about Graphviz availability."""
    if find_dot_binary():
        ver = dot_version()
        print(f"[r2rome] graphviz: {ver}", file=sys.stderr)
    else:
        msg = (
            "[r2rome] WARNING: 'dot' binary not found on PATH.\n"
            "         DOT source will be generated but SVG rendering is unavailable.\n"
            "         Install graphviz (apt/brew/dnf/spack) to enable rendering."
        )
        if warn_only:
            print(msg, file=sys.stderr)
        else:
            print(msg, file=sys.stderr)
            sys.exit(1)


# ---------------------------------------------------------------------------
# Tab-completion helpers (used only when argcomplete is installed)
# ---------------------------------------------------------------------------

def _all_graph_names(graph: Graph) -> List[str]:
    """Collect names of all graphs/subgraphs in the hierarchy."""
    names: List[str] = [graph.name]
    for sg in graph.subgraphs:
        names.extend(_all_graph_names(sg))
    for node in graph.nodes:
        if node.children:
            names.extend(_all_graph_names(node.children))
    return names


def _all_node_names(graph: Graph) -> List[str]:
    """Collect names of every node in the hierarchy."""
    names: List[str] = [n.name for n in graph.nodes]
    for sg in graph.subgraphs:
        names.extend(_all_node_names(sg))
    for node in graph.nodes:
        if node.children:
            names.extend(_all_node_names(node.children))
    return names


def _graph_name_completer(prefix: str, parsed_args: argparse.Namespace, **kwargs):
    """Complete with subgraph names read from the already-typed input file."""
    input_file = getattr(parsed_args, "input_file", None)
    if not input_file:
        return []
    try:
        return _all_graph_names(load(input_file))
    except Exception:
        return []


def _node_name_completer(prefix: str, parsed_args: argparse.Namespace, **kwargs):
    """Complete with node names read from the already-typed input file."""
    input_file = getattr(parsed_args, "input_file", None)
    if not input_file:
        return []
    try:
        return _all_node_names(load(input_file))
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Subcommand: render
# ---------------------------------------------------------------------------

def cmd_render(args: argparse.Namespace) -> int:
    input_path     = Path(args.input_file)
    output_dir     = Path(args.output)
    fmt            = args.fmt
    cdn            = args.cdn
    max_depth      = args.depth
    keep_dot       = args.keep_dot
    ghost_external = args.ghost_external

    if not find_dot_binary():
        print(
            "[r2rome] ERROR: 'dot' binary not found. Cannot render.\n"
            "         Use 'r2rome dot' to generate DOT source without graphviz.\n"
            "         Install: spack install graphviz && spack load graphviz",
            file=sys.stderr,
        )
        return 1

    print(f"[r2rome] loading {input_path}", file=sys.stderr)
    try:
        graph = load(input_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"[r2rome] ERROR: {e}", file=sys.stderr)
        return 1

    registry = build_node_registry(graph)

    svg_dir = output_dir / "svg"
    svg_dir.mkdir(parents=True, exist_ok=True)

    print(f"[r2rome] rendering graphs to {output_dir}", file=sys.stderr)
    try:
        rendered = render_all_levels(
            root=graph,
            output_dir=svg_dir,
            fmt=fmt,
            max_depth=max_depth,
            cleanup=not keep_dot,
            registry=registry,
            ghost_external=ghost_external,
        )
    except RuntimeError as e:
        print(f"[r2rome] ERROR: {e}", file=sys.stderr)
        return 1

    print(f"[r2rome] rendered {len(rendered)} graph(s)", file=sys.stderr)

    levels = _collect_levels(graph)

    graph_data_map = None
    if cdn:
        graph_data_map = {}
        queue = [graph]
        while queue:
            g = queue.pop(0)
            graph_data_map[g.name] = graph_to_json_data(g)
            queue.extend(g.subgraphs)
            for node in g.nodes:
                if node.children:
                    queue.append(node.children)

    html_files = write_all_pages(
        svg_dir=svg_dir,
        output_dir=output_dir,
        level_map=levels,
        cdn=cdn,
        graph_data_map=graph_data_map,
    )

    print(f"[r2rome] wrote {len(html_files)} HTML page(s)", file=sys.stderr)
    index = output_dir / "index.html"
    if index.exists():
        print(f"[r2rome] open: {index.resolve()}", file=sys.stderr)

    return 0


# ---------------------------------------------------------------------------
# Subcommand: dot
# ---------------------------------------------------------------------------

def cmd_dot(args: argparse.Namespace) -> int:
    input_path     = Path(args.input_file)
    level_name     = args.level
    output         = args.output
    max_depth      = args.depth
    ghost_external = args.ghost_external

    try:
        graph = load(input_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"[r2rome] ERROR: {e}", file=sys.stderr)
        return 1

    registry = build_node_registry(graph)

    target = graph
    if level_name:
        found = _find_subgraph_by_name(graph, level_name)
        if found is None:
            print(
                f"[r2rome] ERROR: no subgraph named '{level_name}' found in {input_path}",
                file=sys.stderr,
            )
            return 1
        target = found

    source = to_dot_source(
        target,
        max_depth=max_depth,
        registry=registry,
        ghost_external=ghost_external,
    )

    if output:
        out = Path(output)
        out.write_text(source, encoding="utf-8")
        print(f"[r2rome] written to {out}", file=sys.stderr)
    else:
        print(source)

    return 0


# ---------------------------------------------------------------------------
# Subcommand: init
# ---------------------------------------------------------------------------

_INIT_TEMPLATE = """\
# yaml-language-server: $schema=https://raw.githubusercontent.com/psakievich/r2rome/main/schemas/r2rome.schema.json
name: my_project
title: My Project

nodes:
  - name: epic_one
    label: Epic One
    status: active
    note: First major milestone
    deps: [epic_two]
    graph:
      name: epic_one
      title: Epic One
      nodes:
        - name: task_a
          label: Task A
          status: done
          deps: [task_b]
        - name: task_b
          label: Task B
          status: active

  - name: epic_two
    label: Epic Two
    status: todo
    note: Second major milestone
"""


def cmd_init(args: argparse.Namespace) -> int:
    output = args.output
    if output is None:
        print(_INIT_TEMPLATE, end="")
        return 0

    out = Path(output)
    if out.exists():
        print(
            f"[r2rome] ERROR: '{out}' already exists. "
            "Remove it or choose a different name.",
            file=sys.stderr,
        )
        return 1

    out.write_text(_INIT_TEMPLATE, encoding="utf-8")
    print(f"[r2rome] created {out}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: info
# ---------------------------------------------------------------------------

def cmd_info(args: argparse.Namespace) -> int:
    input_path = Path(args.input_file)

    try:
        graph = load(input_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"[r2rome] ERROR: {e}", file=sys.stderr)
        return 1

    def _print_tree(g: Graph, indent: int = 0) -> None:
        pad  = "  " * indent
        pad2 = "  " * (indent + 1)
        print(f"{pad}graph: {g.name!r}  (title: {g.title!r})")
        for node in g.nodes:
            has_children = node.children or _find_subgraph_by_name(g, node.name)
            marker = " [+]" if has_children else ""
            status = f"  status={node.status}" if node.status else ""
            deps   = f"  deps={node.deps}" if node.deps else ""
            blocks = f"  blocks={node.blocks}" if node.blocks else ""
            print(f"{pad2}node: {node.name!r}{marker}{status}{deps}{blocks}")
        for sg in g.subgraphs:
            _print_tree(sg, indent + 1)
        for node in g.nodes:
            if node.children:
                _print_tree(node.children, indent + 1)

    dot_status = dot_version() or "not found"
    print(f"r2rome {__version__}  |  graphviz: {dot_status}\n")
    _print_tree(graph)
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="r2rome",
        description="Visualize nested project complexity as navigable digraphs.\n"
                    "The road to Rome wasn't built in a day.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"r2rome {__version__}"
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # -- render ---------------------------------------------------------------
    p_render = sub.add_parser(
        "render",
        help="Render a project YAML to SVG + HTML pages",
        description="Render all graph levels to SVG files and wrap them in "
                    "navigable HTML pages.\n\n"
                    "Requires the 'dot' binary (Graphviz) to be on PATH.\n"
                    "Install via: spack install graphviz && spack load graphviz",
    )
    _act = p_render.add_argument("input_file", help="Path to project .yaml/.json file")
    if _HAS_ARGCOMPLETE:
        _act.completer = FilesCompleter(["yaml", "yml", "json"])
    p_render.add_argument(
        "-o", "--output", default="out",
        help="Output directory (default: ./out)",
    )
    p_render.add_argument(
        "--fmt", default="svg", choices=["svg", "png", "pdf"],
        help="Rendered image format (default: svg)",
    )
    p_render.add_argument(
        "--depth", type=int, default=None, metavar="N",
        help="Max subgraph depth to render inline. Deeper nodes become "
             "hyperlinked collapsed nodes. Default: unlimited.",
    )
    p_render.add_argument(
        "--cdn", action="store_true",
        help="Generate interactive CDN-powered HTML pages instead of static SVG. "
             "Requires network access to cdnjs.cloudflare.com when viewing.",
    )
    p_render.add_argument(
        "--keep-dot", action="store_true",
        help="Keep intermediate .dot source files alongside rendered output.",
    )
    p_render.add_argument(
        "--ghost-external", action="store_true",
        help="Render cross-graph deps that point outside the current view as "
             "dashed ghost nodes rather than silently dropping the edge.",
    )
    p_render.set_defaults(func=cmd_render)

    # -- dot ------------------------------------------------------------------
    p_dot = sub.add_parser(
        "dot",
        help="Emit DOT language source (no graphviz binary required)",
        description="Emit DOT language source for a graph or subgraph.\n\n"
                    "Does not require the 'dot' binary — useful for inspecting "
                    "graph structure or piping to graphviz manually:\n\n"
                    "  r2rome dot project.yaml | dot -Tsvg -o out.svg",
    )
    _act = p_dot.add_argument("input_file", help="Path to project .yaml/.json file")
    if _HAS_ARGCOMPLETE:
        _act.completer = FilesCompleter(["yaml", "yml", "json"])
    _act = p_dot.add_argument(
        "--level", default=None, metavar="NAME",
        help="Emit only the named subgraph (default: entire graph)",
    )
    if _HAS_ARGCOMPLETE:
        _act.completer = _graph_name_completer
    p_dot.add_argument(
        "-o", "--output", default=None, metavar="FILE",
        help="Write DOT source to FILE instead of stdout",
    )
    p_dot.add_argument(
        "--depth", type=int, default=None, metavar="N",
        help="Max depth before collapsing subgraphs (default: unlimited)",
    )
    p_dot.add_argument(
        "--ghost-external", action="store_true",
        help="Render cross-graph deps that point outside the current view as "
             "dashed ghost nodes rather than silently dropping the edge.",
    )
    p_dot.set_defaults(func=cmd_dot)

    # -- init -----------------------------------------------------------------
    p_init = sub.add_parser(
        "init",
        help="Create a starter project YAML file",
        description="Write a template project YAML with schema comment, "
                    "example nodes, and a nested subgraph.\n\n"
                    "  r2rome init                    # print template to stdout\n"
                    "  r2rome init my_project.yaml    # write to file",
    )
    _act = p_init.add_argument(
        "output", nargs="?", default=None, metavar="FILE",
        help="Path to write the new project file. Prints to stdout if omitted.",
    )
    if _HAS_ARGCOMPLETE:
        _act.completer = FilesCompleter(["yaml", "yml"])
    p_init.set_defaults(func=cmd_init)

    # -- info -----------------------------------------------------------------
    p_info = sub.add_parser(
        "info",
        help="Print a summary of the graph structure",
    )
    _act = p_info.add_argument("input_file", help="Path to project .yaml/.json file")
    if _HAS_ARGCOMPLETE:
        _act.completer = FilesCompleter(["yaml", "yml", "json"])
    p_info.set_defaults(func=cmd_info)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = build_parser()
    if _HAS_ARGCOMPLETE:
        argcomplete.autocomplete(parser)
    args   = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
