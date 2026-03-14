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
  - Nodes with a 'graph:' key render as inline DOT cluster subgraphs,
    with compound=true so edges to/from clusters connect at the boundary
  - assemble() accepts an optional max_depth to collapse subgraphs into
    hyperlinked nodes rather than recursing indefinitely
"""

from __future__ import annotations

import warnings
from typing import Dict, List, Optional, Set

from graphviz import Digraph

from r2rome.model import (
    THEMES,
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

def _add_node(node: GraphNode, graph: Digraph, theme: Optional[Dict] = None) -> None:
    """Add a single GraphNode to a graphviz.Digraph."""
    if theme is None:
        theme = THEMES["dark"]
    attrs = {**theme["node_attr"], **node.effective_dot_attrs(theme["status_style"])}
    # Set SVG id to node name so CDN tooltip JS can look up notes by id
    attrs.setdefault("id", node.name)
    # Set native SVG tooltip when a note is present
    if node.note:
        attrs.setdefault("tooltip", node.note)
    graph.node(node.name, label=node.label or node.name, **attrs)


def _add_edges(
    node: GraphNode,
    graph: Digraph,
    known_names: Set[str],
    cluster_names: Optional[Set[str]] = None,
    theme: Optional[Dict] = None,
) -> None:
    """Add deps and blocks edges from a node into the graph.

    Edges referencing names not in known_names are skipped with a warning
    rather than hard-failing, so partial graphs render gracefully.

    When cluster_names is provided, edges to/from cluster nodes use
    lhead/ltail so graphviz draws arrows to the cluster boundary rather
    than to the invisible anchor node inside it (requires compound=true
    on the parent graph).
    """
    if theme is None:
        theme = THEMES["dark"]
    cluster_names = cluster_names or set()

    for dep in node.deps:
        if dep not in known_names:
            warnings.warn(
                f"Node '{node.name}' has dep '{dep}' which is not defined "
                "in this graph level — edge skipped.",
                stacklevel=3,
            )
            continue
        edge_attrs: Dict[str, str] = {}
        if node.name in cluster_names:
            edge_attrs["ltail"] = f"cluster_{node.name}"
        if dep in cluster_names:
            edge_attrs["lhead"] = f"cluster_{dep}"
        graph.edge(node.name, dep, **edge_attrs)

    for blocked in node.blocks:
        if blocked not in known_names:
            warnings.warn(
                f"Node '{node.name}' blocks '{blocked}' which is not defined "
                "in this graph level — edge skipped.",
                stacklevel=3,
            )
            continue
        edge_attrs = dict(theme["blocks_edge"])
        if node.name in cluster_names:
            edge_attrs["ltail"] = f"cluster_{node.name}"
        if blocked in cluster_names:
            edge_attrs["lhead"] = f"cluster_{blocked}"
        graph.edge(node.name, blocked, **edge_attrs)


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
# Cluster builder for nodes with inline child graphs
# ---------------------------------------------------------------------------

def _build_node_cluster(
    node: GraphNode,
    current_depth: int,
    max_depth: Optional[int],
    output_dir: Optional[str],
    theme: Optional[Dict] = None,
) -> Digraph:
    """Build a DOT cluster subgraph from a node's child graph.

    The node itself becomes an invisible anchor inside the cluster so that
    edges declared with deps/blocks using this node's name still resolve.
    With compound=true on the parent graph, graphviz routes those edges to
    the cluster boundary rather than to the invisible anchor.
    """
    if theme is None:
        theme = THEMES["dark"]
    child_graph = node.children  # type: ignore[union-attr]

    # Cluster border color follows the node's status
    border_color = theme["cluster_border_fallback"]
    if node.status and node.status in theme["status_style"]:
        border_color = theme["status_style"][node.status].get("color", border_color)

    cluster_attr: Dict[str, str] = {
        "label":     node.label or node.name,
        "style":     "filled",
        "fillcolor": theme["cluster_fill"],
        "color":     border_color,
        "fontcolor": theme["graph_attr"]["fontcolor"],
        "fontname":  theme["graph_attr"]["fontname"],
    }
    if output_dir is not None:
        cluster_attr["href"]   = f"{node.name}.html"
        cluster_attr["target"] = "_self"

    cluster = Digraph(
        name=f"cluster_{node.name}",
        graph_attr=cluster_attr,
    )

    # Invisible anchor so edges targeting this node's name still work
    cluster.node(
        node.name,
        label="",
        style="invis",
        width="0.01",
        height="0.01",
    )

    child_known: Set[str] = {n.name for n in child_graph.nodes}
    child_clusters: Set[str] = set()

    for child_node in child_graph.nodes:
        at_limit = max_depth is not None and current_depth >= max_depth
        has_children = child_node.children is not None

        if at_limit and has_children:
            href_attrs: Dict[str, str] = {}
            if output_dir is not None:
                href_attrs["href"]      = f"{child_node.name}.html"
                href_attrs["fontcolor"] = "blue"
                href_attrs["tooltip"]   = f"Click to expand {child_node.label or child_node.name}"
            collapsed_attrs = {
                **theme["node_attr"],
                **child_node.effective_dot_attrs(theme["status_style"]),
                **href_attrs,
                "peripheries": "2",
            }
            cluster.node(child_node.name, label=child_node.label or child_node.name, **collapsed_attrs)
        elif has_children:
            child_clusters.add(child_node.name)
            sub_cluster = _build_node_cluster(
                child_node,
                current_depth=current_depth + 1,
                max_depth=max_depth,
                output_dir=output_dir,
                theme=theme,
            )
            cluster.subgraph(sub_cluster)
        else:
            _add_node(child_node, cluster, theme=theme)

    for child_node in child_graph.nodes:
        _add_edges(child_node, cluster, child_known, cluster_names=child_clusters, theme=theme)

    return cluster


# ---------------------------------------------------------------------------
# Recursive graph assembly with depth limiting
# ---------------------------------------------------------------------------

def build_digraph(
    graph: Graph,
    parent: Optional[Digraph] = None,
    current_depth: int = 0,
    max_depth: Optional[int] = None,
    output_dir: Optional[str] = None,
    theme: Optional[Dict] = None,
) -> Digraph:
    """Recursively assemble a Graph into a graphviz.Digraph.

    Nodes with a child graph (from the 'graph:' YAML key) are rendered as
    inline DOT cluster subgraphs.  compound=true is set on the root digraph
    so that edges targeting clusters connect at the cluster boundary.

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

    # Select theme once at the root; propagate it through all recursive calls
    if theme is None:
        scheme = getattr(graph, "color_scheme", "dark")
        theme = THEMES.get(scheme, THEMES["dark"])

    g_attr = dict(graph.graph_attr)
    if is_root:
        g_attr["compound"] = "true"

    dot = Digraph(
        name=graph.name if is_root else graph.dot_name,
        graph_attr=g_attr,
        edge_attr=theme["edge_attr"],
    )
    target = dot

    known_names: Set[str] = {n.name for n in graph.nodes}

    # Nodes that will be rendered as inline clusters at this level
    inline_clusters: Set[str] = set()

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
                **theme["node_attr"],
                **node.effective_dot_attrs(theme["status_style"]),
                **href_attrs,
                "peripheries": "2",   # double border signals expandability
            }
            target.node(node.name, label=node.label or node.name, **collapsed_attrs)
        elif node.children is not None:
            # Render child graph as an inline cluster
            inline_clusters.add(node.name)
            cluster = _build_node_cluster(
                node,
                current_depth=current_depth + 1,
                max_depth=max_depth,
                output_dir=output_dir,
                theme=theme,
            )
            target.subgraph(cluster)
        else:
            _add_node(node, target, theme=theme)

    # Add edges after all nodes/clusters are placed
    for node in graph.nodes:
        _add_edges(node, target, known_names, cluster_names=inline_clusters, theme=theme)

    # Recurse into top-level subgraphs (graph.graphs in YAML) unless at depth limit
    for subgraph in graph.subgraphs:
        if max_depth is not None and current_depth >= max_depth:
            continue
        sub_dot = build_digraph(
            subgraph,
            parent=target,
            current_depth=current_depth + 1,
            max_depth=max_depth,
            output_dir=output_dir,
            theme=theme,
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
