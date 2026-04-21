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
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from graphviz import Digraph

from r2rome.model import (
    THEMES,
    Graph,
    GraphNode,
    resolve_cross_ref,
)


# ---------------------------------------------------------------------------
# Cross-graph assembly state
# ---------------------------------------------------------------------------

@dataclass
class _CrossCtx:
    """Mutable state threaded through the recursive build for cross-graph edges.

    Initialized once at the root call of build_digraph and passed into every
    _add_edges call so deferred edges and ghost nodes accumulate in one place
    for final emission at the root level.
    """
    registry:      Dict[str, GraphNode]        # full-path → node
    renderable:    Set[str]                    # short names present in this DOT scope
    all_clusters:  Set[str]                    # short names rendered as clusters
    deferred:      List[Tuple[str, str, str]]  # (src, tgt, "dep"|"blocks")
    ghost_nodes:   Dict[str, GraphNode]         # ghost_dot_id → original node
    ghost_edges:   List[Tuple[str, str, str]]  # (src, ghost_dot_id, "dep"|"blocks")
    ghost_external: bool
    theme:         Dict[str, Any]


def _ghost_dot_id(full_path: str) -> str:
    """Convert a full ::path to a safe DOT node identifier."""
    return "__ghost__" + full_path.replace("::", "__")


def _collect_renderable(graph: Graph) -> Set[str]:
    """Recursively collect all node short-names in a graph tree."""
    names: Set[str] = {n.name for n in graph.nodes}
    for sg in graph.subgraphs:
        names |= _collect_renderable(sg)
    for node in graph.nodes:
        if node.children:
            names |= _collect_renderable(node.children)
    return names


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
    cross_ctx: Optional[_CrossCtx] = None,
) -> None:
    """Add deps and blocks edges from a node into the graph.

    Local edges (no ``::`` in the name) behave as before — skipped with a
    warning when the target is unknown in this graph level.

    Cross-graph edges (name contains ``::``) are resolved against the registry
    in *cross_ctx* when provided:
      - Target exists in the render scope → deferred to root for emission.
      - Target outside the render scope and ghost_external=True → a ghost node
        and edge are recorded for emission at root.
      - Otherwise → warning and skip.

    When cluster_names is provided, edges to/from cluster nodes use
    lhead/ltail so graphviz draws arrows to the cluster boundary rather
    than to the invisible anchor node inside it (requires compound=true
    on the parent graph).
    """
    if theme is None:
        theme = THEMES["dark"]
    cluster_names = cluster_names or set()

    def _handle_ref(ref: str, kind: str) -> None:
        is_cross = "::" in ref

        if not is_cross:
            # Local reference — existing behaviour
            if ref not in known_names:
                warnings.warn(
                    f"Node '{node.name}' has {kind} '{ref}' which is not defined "
                    "in this graph level — edge skipped.",
                    stacklevel=4,
                )
                return
            edge_attrs: Dict[str, str] = {}
            if kind == "blocks":
                edge_attrs = dict(theme["blocks_edge"])
            if node.name in cluster_names:
                edge_attrs["ltail"] = f"cluster_{node.name}"
            if ref in cluster_names:
                edge_attrs["lhead"] = f"cluster_{ref}"
            graph.edge(node.name, ref, **edge_attrs)
            return

        # Cross-graph reference
        if cross_ctx is None:
            warnings.warn(
                f"Node '{node.name}' has cross-graph {kind} '{ref}' but no "
                "registry was provided — edge skipped. Pass registry= to "
                "build_digraph() to enable cross-graph edges.",
                stacklevel=4,
            )
            return

        resolved = resolve_cross_ref(ref, cross_ctx.registry)
        if resolved is None:
            warnings.warn(
                f"Node '{node.name}' has cross-graph {kind} '{ref}' which "
                "could not be resolved — edge skipped.",
                stacklevel=4,
            )
            return

        _full_path, _tgt_node = resolved
        tgt_short = _tgt_node.name

        if tgt_short in cross_ctx.renderable:
            # Both endpoints will be in the DOT graph — defer to root
            cross_ctx.deferred.append((node.name, tgt_short, kind))
        elif cross_ctx.ghost_external:
            ghost_id = _ghost_dot_id(_full_path)
            cross_ctx.ghost_nodes[ghost_id] = _tgt_node  # store full node
            cross_ctx.ghost_edges.append((node.name, ghost_id, kind))
        else:
            warnings.warn(
                f"Node '{node.name}' has cross-graph {kind} '{ref}' which "
                "targets a node outside the current render scope — edge "
                "skipped. Use --ghost-external to render it as a ghost node.",
                stacklevel=4,
            )

    for dep in node.deps:
        _handle_ref(dep, "dep")

    for blocked in node.blocks:
        _handle_ref(blocked, "blocks")


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
    cross_ctx: Optional[_CrossCtx] = None,
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
            if cross_ctx is not None:
                cross_ctx.all_clusters.add(child_node.name)
            sub_cluster = _build_node_cluster(
                child_node,
                current_depth=current_depth + 1,
                max_depth=max_depth,
                output_dir=output_dir,
                theme=theme,
                cross_ctx=cross_ctx,
            )
            cluster.subgraph(sub_cluster)
        else:
            _add_node(child_node, cluster, theme=theme)

    for child_node in child_graph.nodes:
        _add_edges(
            child_node, cluster, child_known,
            cluster_names=child_clusters, theme=theme, cross_ctx=cross_ctx,
        )

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
    registry: Optional[Dict[str, GraphNode]] = None,
    ghost_external: bool = False,
    _cross_ctx: Optional[_CrossCtx] = None,
) -> Digraph:
    """Recursively assemble a Graph into a graphviz.Digraph.

    Nodes with a child graph (from the 'graph:' YAML key) are rendered as
    inline DOT cluster subgraphs.  compound=true is set on the root digraph
    so that edges targeting clusters connect at the cluster boundary.

    Args:
        graph:          The Graph to assemble.
        parent:         Parent Digraph to attach a subgraph to. None = root.
        current_depth:  Current recursion depth (0 = root call).
        max_depth:      Maximum depth before collapsing subgraphs to linked
                        nodes. None = unlimited (original behavior).
        output_dir:     Base output directory for generating href links on
                        collapsed nodes. Required when max_depth is set.
        registry:       Full node registry from build_node_registry(). When
                        provided, deps/blocks containing '::' are resolved as
                        cross-graph references.
        ghost_external: When True, deps/blocks that point outside the current
                        render scope are rendered as dashed ghost nodes rather
                        than being silently skipped.

    Returns:
        The assembled Digraph (same object as parent if parent was provided).
    """
    is_root = parent is None

    # Select theme once at the root; propagate it through all recursive calls
    if theme is None:
        scheme = getattr(graph, "color_scheme", "dark")
        theme = THEMES.get(scheme, THEMES["dark"])

    # Initialise cross-graph context once at the root call
    if is_root and registry is not None and _cross_ctx is None:
        _cross_ctx = _CrossCtx(
            registry=registry,
            renderable=_collect_renderable(graph),
            all_clusters=set(),
            deferred=[],
            ghost_nodes={},
            ghost_edges=[],
            ghost_external=ghost_external,
            theme=theme,
        )

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
            if _cross_ctx is not None:
                _cross_ctx.all_clusters.add(node.name)
            cluster = _build_node_cluster(
                node,
                current_depth=current_depth + 1,
                max_depth=max_depth,
                output_dir=output_dir,
                theme=theme,
                cross_ctx=_cross_ctx,
            )
            target.subgraph(cluster)
        else:
            _add_node(node, target, theme=theme)

    # Add edges after all nodes/clusters are placed
    for node in graph.nodes:
        _add_edges(
            node, target, known_names,
            cluster_names=inline_clusters, theme=theme, cross_ctx=_cross_ctx,
        )

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
            _cross_ctx=_cross_ctx,
        )
        target.subgraph(sub_dot)

    # At root: emit ghost nodes, ghost edges, and deferred cross-graph edges
    if is_root and _cross_ctx is not None:
        _emit_ghosts_and_deferred(dot, _cross_ctx)

    if is_root:
        return dot

    if parent is not None:
        parent.subgraph(dot)

    return parent or dot


def _emit_ghosts_and_deferred(dot: Digraph, ctx: _CrossCtx) -> None:
    """Emit ghost nodes and all deferred cross-graph edges into the root Digraph."""
    theme = ctx.theme

    for ghost_id, orig_node in ctx.ghost_nodes.items():
        full_path = ghost_id[len("__ghost__"):].replace("__", "::")
        # Start from the original node's full attributes (label, status colour, shape…)
        attrs: Dict[str, str] = {
            **theme["node_attr"],
            **orig_node.effective_dot_attrs(theme["status_style"]),
        }
        # Ghost overrides: dashed border retains status colour; fill goes to background
        attrs["style"] = "dashed,filled"
        attrs["fillcolor"] = theme["graph_attr"]["bgcolor"]
        # Tooltip: prefer the node's own note, fall back to full path
        attrs["tooltip"] = orig_node.note if orig_node.note else full_path
        dot.node(ghost_id, label=orig_node.label or orig_node.name, **attrs)

    for src, ghost_id, kind in ctx.ghost_edges:
        if kind == "blocks":
            dot.edge(src, ghost_id, **dict(theme["blocks_edge"]))
        else:
            dot.edge(src, ghost_id)

    for src, tgt, kind in ctx.deferred:
        edge_attrs: Dict[str, str] = {}
        if kind == "blocks":
            edge_attrs = dict(theme["blocks_edge"])
        if src in ctx.all_clusters:
            edge_attrs["ltail"] = f"cluster_{src}"
        if tgt in ctx.all_clusters:
            edge_attrs["lhead"] = f"cluster_{tgt}"
        dot.edge(src, tgt, **edge_attrs)


def _find_subgraph(graph: Graph, node_name: str) -> Optional[Graph]:
    """Find a direct subgraph whose name matches a node name."""
    for sg in graph.subgraphs:
        if sg.name == node_name:
            return sg
    return None
