"""
r2rome.assemble
~~~~~~~~~~~~~~~
Wires nodes and edges into a graphviz.Digraph.

This module is the evolution of software_graphs.py from the original
process.py tooling.  The public API is intentionally similar so existing
YAML files continue to work without changes.

Key differences from the original:
  - GraphNode is now a dataclass (defined in model.py)
  - 'blocks' edges are supported alongside 'deps', rendered with a distinct
    dashed red style
  - ci_coloring works on both deps and blocks for full propagation
  - assemble() accepts an optional max_depth to collapse subgraphs into
    hyperlinked nodes rather than recursing indefinitely
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set

from graphviz import Digraph

from r2rome.model import (
    BLOCKS_EDGE_STYLE,
    DEFAULT_NODE_ATTR,
    Graph,
    GraphNode,
)


# ---------------------------------------------------------------------------
# Node coloring (CI impact analysis — preserved from original)
# ---------------------------------------------------------------------------

def color_nodes(
    nodes: List[GraphNode],
    change_set: Set[str],
    color: str,
) -> List[GraphNode]:
    """Apply a fill color to nodes whose name is in change_set.

    Returns only the nodes that were actually colored (i.e. were in the
    change_set), so the caller can chain propagation.
    """
    colored = []
    for node in nodes:
        if node.name in change_set:
            node.dot_attrs["color"] = color
            node.dot_attrs["style"] = "filled"
            colored.append(node)
    return colored


def ci_coloring(nodes: List[GraphNode], change_set: Set[str]) -> None:
    """Propagate CI impact colors through a flat node list.

    Nodes in change_set are colored red.  Everything that transitively
    depends on them (via deps) or is blocked by them (via blocks) is
    colored orange.

    Propagation rules:
      - deps are traversed in reverse: if X changes, nodes that have X in
        their deps list are impacted (they depend on X).
      - blocks are traversed forward: if X changes, nodes that X blocks
        are also impacted.

    This mutates nodes in place — call clear_attrs() afterwards if you
    need to reset for another pass.
    """
    # Build reverse-deps map: name -> set of node names that dep on it
    reverse_deps: Dict[str, Set[str]] = {n.name: set() for n in nodes}
    for n in nodes:
        for dep in n.deps:
            if dep in reverse_deps:
                reverse_deps[dep].add(n.name)

    changes = color_nodes(nodes, change_set, "red")
    while changes:
        new_change_set: Set[str] = set()
        for n in changes:
            # Nodes that depend on this one are impacted (reverse deps)
            new_change_set.update(reverse_deps.get(n.name, set()))
            # Nodes this one blocks are also impacted (forward blocks)
            new_change_set.update(n.blocks)
        changes = color_nodes(nodes, new_change_set, "orange")


def clear_attrs(nodes: List[GraphNode]) -> None:
    """Reset dot_attrs on all nodes (e.g. between CI coloring passes)."""
    for node in nodes:
        node.dot_attrs.clear()


# ---------------------------------------------------------------------------
# Core assembly
# ---------------------------------------------------------------------------

def _add_node(node: GraphNode, graph: Digraph) -> None:
    """Add a single GraphNode to a graphviz.Digraph."""
    attrs = {**DEFAULT_NODE_ATTR, **node.effective_dot_attrs()}
    graph.node(node.name, label=node.label or node.name, **attrs)


def _add_edges(node: GraphNode, graph: Digraph, known_names: Set[str]) -> None:
    """Add deps and blocks edges from a node into the graph.

    Edges referencing names not in known_names are skipped with a warning
    rather than hard-failing, so partial graphs render gracefully.
    """
    for dep in node.deps:
        if dep not in known_names:
            import warnings
            warnings.warn(
                f"Node '{node.name}' has dep '{dep}' which is not defined "
                "in this graph level — edge skipped.",
                stacklevel=3,
            )
            continue
        graph.edge(node.name, dep)

    for blocked in node.blocks:
        if blocked not in known_names:
            import warnings
            warnings.warn(
                f"Node '{node.name}' blocks '{blocked}' which is not defined "
                "in this graph level — edge skipped.",
                stacklevel=3,
            )
            continue
        graph.edge(node.name, blocked, **BLOCKS_EDGE_STYLE)


def assemble(nodes: List[GraphNode], graph: Digraph) -> None:
    """Wire a flat list of GraphNodes into a graphviz.Digraph.

    Mirrors the original assemble() signature for compatibility.
    Nodes are added first, then all edges (deps and blocks).
    """
    known_names: Set[str] = {n.name for n in nodes}
    for node in nodes:
        _add_node(node, graph)
    for node in nodes:
        _add_edges(node, graph, known_names)


# ---------------------------------------------------------------------------
# Recursive graph assembly with depth limiting
# ---------------------------------------------------------------------------

def build_digraph(
    graph: Graph,
    parent: Optional[Digraph] = None,
    current_depth: int = 0,
    max_depth: Optional[int] = None,
    output_dir: Optional[str] = None,
) -> Digraph:
    """Recursively assemble a Graph into a graphviz.Digraph.

    Args:
        graph:         The Graph to assemble.
        parent:        Parent Digraph to attach a subgraph to. None = root.
        current_depth: Current recursion depth (0 = root call).
        max_depth:     Maximum depth before collapsing subgraphs to linked
                       nodes. None = unlimited (original behavior).
        output_dir:    Base output directory for generating href links on
                       collapsed nodes. Required when max_depth is set.

    Returns:
        The assembled Digraph (same object as parent if parent was provided).
    """
    is_root = parent is None
    dot = Digraph(name=graph.dot_name, graph_attr=graph.graph_attr) if not is_root \
        else Digraph(name=graph.name, graph_attr=graph.graph_attr)

    target = dot

    known_names: Set[str] = {n.name for n in graph.nodes}

    for node in graph.nodes:
        at_depth_limit = (max_depth is not None and current_depth >= max_depth)
        has_children   = node.children is not None or bool(
            _find_subgraph(graph, node.name)
        )

        if at_depth_limit and has_children:
            # Collapse: render as a plain node with an href to its detail page
            href_attrs: Dict[str, str] = {}
            if output_dir is not None:
                href_attrs["href"]      = f"{node.name}.html"
                href_attrs["fontcolor"] = "blue"
                href_attrs["tooltip"]   = f"Click to expand {node.label or node.name}"
            collapsed_attrs = {
                **DEFAULT_NODE_ATTR,
                **node.effective_dot_attrs(),
                **href_attrs,
                "peripheries": "2",   # double border signals expandability
            }
            target.node(node.name, label=node.label or node.name, **collapsed_attrs)
        else:
            _add_node(node, target)

        _add_edges(node, target, known_names)

    # Recurse into subgraphs unless at depth limit
    for subgraph in graph.subgraphs:
        if max_depth is not None and current_depth >= max_depth:
            # Already handled above as collapsed nodes; skip recursion
            continue
        sub_dot = build_digraph(
            subgraph,
            parent=target,
            current_depth=current_depth + 1,
            max_depth=max_depth,
            output_dir=output_dir,
        )
        target.subgraph(sub_dot)

    if is_root:
        return dot

    if parent is not None:
        parent.subgraph(dot)

    return parent or dot


def _find_subgraph(graph: Graph, node_name: str) -> Optional[Graph]:
    """Find a direct subgraph whose name matches a node name."""
    for sg in graph.subgraphs:
        if sg.name == node_name:
            return sg
    return None
