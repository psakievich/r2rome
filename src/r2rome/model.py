"""
r2rome.model
~~~~~~~~~~~~
Data model for project graphs.

A Graph is a recursive structure: it has nodes and edges, and any node
may itself contain a child Graph (making it a subgraph when rendered).

YAML schema
-----------
  name: root                     # required, unique identifier (no spaces)
  title: My Project              # optional display title; falls back to name
  graphs:                        # list of named subgraphs (mirrors process.py)
    - name: epic_one
      title: Epic One            # optional
      cluster: true              # default true; wraps subgraph in a DOT cluster
      graph_attr:                # optional DOT graph attributes
        rankdir: LR
      nodes:
        - name: task_a           # required
          label: Task A          # optional display label; falls back to name
          deps: [task_b]         # outgoing edges: task_a -> task_b
          blocks: [task_c]       # outgoing edges with blocked styling: task_a -> task_c
          status: active         # optional: done | active | todo | blocked
          note: "free text"      # optional annotation
          href: "./sub.html"     # optional; passed through to DOT as hyperlink
          # any other key/value pairs are forwarded as DOT node attributes
      graphs:                    # nested subgraphs (recursive)
        - name: nested_epic
          nodes: [...]
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_STATUSES = {"done", "active", "todo", "blocked"}

# DOT styling per status — fill and font colors
STATUS_STYLE: Dict[str, Dict[str, str]] = {
    "done":    {"fillcolor": "#0f2e22", "fontcolor": "#3dcf8e", "color": "#3dcf8e"},
    "active":  {"fillcolor": "#092820", "fontcolor": "#4af0c4", "color": "#4af0c4"},
    "todo":    {"fillcolor": "#151b29", "fontcolor": "#7b8baa", "color": "#3a4460"},
    "blocked": {"fillcolor": "#2a1212", "fontcolor": "#f06a6a", "color": "#f06a6a"},
}

# Styling for 'blocks' edges — visually distinct from deps
BLOCKS_EDGE_STYLE: Dict[str, str] = {
    "style": "dashed",
    "color": "#f06a6a",
    "fontcolor": "#f06a6a",
}

DEFAULT_GRAPH_ATTR: Dict[str, str] = {
    "rankdir": "LR",
    "labelloc": "t",
    "bgcolor": "#0d0f14",
    "fontcolor": "#c8d0e0",
    "fontname": "JetBrains Mono, monospace",
}

DEFAULT_NODE_ATTR: Dict[str, str] = {
    "style": "filled",
    "fillcolor": "#151b29",
    "color": "#3a4460",
    "fontcolor": "#7b8baa",
    "fontname": "JetBrains Mono, monospace",
    "shape": "box",
}


# ---------------------------------------------------------------------------
# GraphNode
# ---------------------------------------------------------------------------

# Reserved keys consumed by r2rome — not forwarded to DOT as node attributes
_RESERVED_NODE_KEYS = {"name", "label", "deps", "blocks", "status", "note", "graph", "graphs"}


@dataclass
class GraphNode:
    """A single node in a project graph.

    Attributes:
        name:       Unique identifier. Used as the DOT node ID.
        label:      Human-readable display label. Falls back to name.
        deps:       Names of nodes this node depends on (outgoing edges).
        blocks:     Names of nodes this node is blocking (outgoing dashed edges).
        status:     One of done | active | todo | blocked.
        note:       Free-text annotation shown in tooltips/exports.
        dot_attrs:  Extra DOT node attributes forwarded verbatim (e.g. href, shape).
        children:   Optional nested Graph (makes this node expandable).
    """

    name: str
    label: Optional[str] = None
    deps: List[str] = field(default_factory=list)
    blocks: List[str] = field(default_factory=list)
    status: Optional[str] = None
    note: Optional[str] = None
    dot_attrs: Dict[str, Any] = field(default_factory=dict)
    children: Optional["Graph"] = None

    def __post_init__(self) -> None:
        if self.label is None:
            self.label = self.name
        if self.status and self.status not in VALID_STATUSES:
            raise ValueError(
                f"Node '{self.name}': invalid status '{self.status}'. "
                f"Must be one of: {sorted(VALID_STATUSES)}"
            )

    def effective_dot_attrs(self) -> Dict[str, str]:
        """Return merged DOT node attributes including status styling."""
        attrs: Dict[str, str] = {}
        if self.status and self.status in STATUS_STYLE:
            attrs.update(STATUS_STYLE[self.status])
        # href triggers blue font in original code — preserve that
        if "href" in self.dot_attrs:
            attrs["fontcolor"] = "blue"
        attrs.update(self.dot_attrs)
        return attrs

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GraphNode":
        """Parse a node from a YAML dict, forwarding unknown keys to DOT."""
        if "name" not in data:
            raise ValueError(f"Node missing required field 'name': {data}")

        name   = data["name"]
        label  = data.get("label")
        deps   = _coerce_list(data.get("deps", []), f"node '{name}' deps")
        blocks = _coerce_list(data.get("blocks", []), f"node '{name}' blocks")
        status = data.get("status")
        note   = data.get("note")

        # Forward unrecognised keys as DOT attributes
        dot_attrs = {
            k: str(v)
            for k, v in data.items()
            if k not in _RESERVED_NODE_KEYS
        }

        # Nested subgraph: 'graph' (singular dict) or 'graphs' (list of subgraphs)
        children: Optional[Graph] = None
        if "graph" in data:
            children = Graph.from_dict(data["graph"])
        elif "graphs" in data:
            children = Graph.from_dict({"name": name, "graphs": data["graphs"]})

        return cls(
            name=name,
            label=label,
            deps=deps,
            blocks=blocks,
            status=status,
            note=note,
            dot_attrs=dot_attrs,
            children=children,
        )


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

@dataclass
class Graph:
    """A named graph (or subgraph) containing nodes and optional child graphs.

    Mirrors the structure from process.py: a graph has nodes and may have
    nested graphs.  Edges are derived from node.deps and node.blocks rather
    than being declared separately.
    """

    name: str
    title: Optional[str] = None
    cluster: bool = True
    graph_attr: Dict[str, str] = field(default_factory=dict)
    nodes: List[GraphNode] = field(default_factory=list)
    subgraphs: List["Graph"] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.title is None:
            self.title = self.name
        # Merge with defaults — caller overrides take precedence
        merged = dict(DEFAULT_GRAPH_ATTR)
        merged.update(self.graph_attr)
        merged["label"] = self.title
        self.graph_attr = merged

    @property
    def dot_name(self) -> str:
        """DOT subgraph identifier — prefixed with 'cluster_' when cluster=True."""
        return f"cluster_{self.name}" if self.cluster else self.name

    def all_node_names(self) -> List[str]:
        """Return names of all direct nodes in this graph."""
        return [n.name for n in self.nodes]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Graph":
        """Parse a Graph from a YAML dict (top-level or nested)."""
        name       = data.get("name", "root")
        title      = data.get("title", name)
        cluster    = data.get("cluster", True)
        graph_attr = data.get("graph_attr", {})

        nodes: List[GraphNode] = [
            GraphNode.from_dict(n) for n in data.get("nodes", [])
        ]

        subgraphs: List[Graph] = [
            Graph.from_dict(g) for g in data.get("graphs", [])
        ]

        return cls(
            name=name,
            title=title,
            cluster=cluster,
            graph_attr=graph_attr,
            nodes=nodes,
            subgraphs=subgraphs,
        )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load(path: os.PathLike) -> Graph:
    """Load and parse a YAML project file into a Graph.

    Args:
        path: Path to a .yaml or .yml file.

    Returns:
        A fully parsed Graph.

    Raises:
        FileNotFoundError: If the path does not exist.
        yaml.YAMLError:    If the file is not valid YAML.
        ValueError:        If the graph schema is invalid.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Project file not found: {path}")

    with path.open("r") as fh:
        data = yaml.safe_load(fh)

    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping at the top level, got: {type(data)}")

    return Graph.from_dict(data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce_list(value: Any, context: str) -> List[str]:
    """Ensure a deps/blocks value is a list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        # Allow a single dep as a bare string
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    raise ValueError(
        f"{context}: expected a list or string, got {type(value).__name__}"
    )
