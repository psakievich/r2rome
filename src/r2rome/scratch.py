"""
r2rome.scratch
~~~~~~~~~~~~~~
Interactive scratch mode for streaming node ideas into a graph file.

Mini-language (one mutation per line):

  name                 create / touch a node
  name: Label          set display label
  name active          set status  (active | done | todo | blocked)
  name -> dep          add dep edge
  name -| blocked      add blocks edge
  name "note text"     set note
  name ::path::node    create node with a cross-graph dep in one step

Tab completes node names (and :: paths) on dep / blocks entries.
Empty line, ``q``, or Ctrl-D exits and saves.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set, Tuple

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from r2rome.model import VALID_STATUSES, build_node_registry, load


# ---------------------------------------------------------------------------
# Mutation types
# ---------------------------------------------------------------------------

@dataclass
class TouchNode:
    name: str

@dataclass
class SetLabel:
    name: str
    label: str

@dataclass
class SetStatus:
    name: str
    status: str

@dataclass
class AddDep:
    name: str
    dep: str

@dataclass
class AddBlocks:
    name: str
    blocked: str

@dataclass
class SetNote:
    name: str
    note: str

Mutation = TouchNode | SetLabel | SetStatus | AddDep | AddBlocks | SetNote


# ---------------------------------------------------------------------------
# Line parser
# ---------------------------------------------------------------------------

_STATUS_RE  = re.compile(r'^(\S+)\s+(' + '|'.join(VALID_STATUSES) + r')$')
_LABEL_RE   = re.compile(r'^(\S+):\s+(.+)$')
_DEP_RE     = re.compile(r'^(\S+)\s+->\s+(\S+)$')
_BLOCKS_RE  = re.compile(r'^(\S+)\s+-\|\s+(\S+)$')
_NOTE_RE    = re.compile(r'^(\S+)\s+"(.+)"$')
_TOUCH_RE   = re.compile(r'^([\w:]+)$')


def parse_line(line: str) -> Optional[Mutation]:
    """Parse one scratch line into a Mutation, or None if unrecognised."""
    line = line.strip()
    if not line:
        return None

    m = _STATUS_RE.match(line)
    if m:
        return SetStatus(m.group(1), m.group(2))

    m = _LABEL_RE.match(line)
    if m:
        return SetLabel(m.group(1), m.group(2).strip())

    m = _DEP_RE.match(line)
    if m:
        return AddDep(m.group(1), m.group(2))

    m = _BLOCKS_RE.match(line)
    if m:
        return AddBlocks(m.group(1), m.group(2))

    m = _NOTE_RE.match(line)
    if m:
        return SetNote(m.group(1), m.group(2))

    m = _TOUCH_RE.match(line)
    if m:
        return TouchNode(m.group(1))

    return None


# ---------------------------------------------------------------------------
# YAML mutation helpers
# ---------------------------------------------------------------------------

def _ryaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.default_flow_style = False
    y.width = 120
    return y


def _load_raw(path: Path) -> Tuple[YAML, CommentedMap]:
    y = _ryaml()
    data = y.load(path)
    if not isinstance(data, CommentedMap):
        raise ValueError(f"Expected a YAML mapping at top level in {path}")
    return y, data


def _save_raw(y: YAML, data: CommentedMap, path: Path) -> None:
    y.dump(data, path)


def _ensure_nodes_list(data: CommentedMap) -> CommentedSeq:
    if "nodes" not in data or data["nodes"] is None:
        data["nodes"] = CommentedSeq()
    return data["nodes"]  # type: ignore[return-value]


def _find_node(nodes: CommentedSeq, name: str) -> Optional[CommentedMap]:
    for entry in nodes:
        if isinstance(entry, CommentedMap) and entry.get("name") == name:
            return entry
    return None


def _ensure_node(data: CommentedMap, name: str) -> Tuple[CommentedMap, bool]:
    """Return (node_dict, created). Converts string shorthand to dict if needed."""
    nodes = _ensure_nodes_list(data)

    # Handle string-shorthand entries
    for i, entry in enumerate(nodes):
        if isinstance(entry, str) and entry == name:
            new_entry: CommentedMap = CommentedMap({"name": name})
            nodes[i] = new_entry
            return new_entry, False

    node = _find_node(nodes, name)
    if node is not None:
        return node, False

    new_node: CommentedMap = CommentedMap({"name": name})
    nodes.append(new_node)
    return new_node, True


def _list_field(node: CommentedMap, field: str) -> CommentedSeq:
    if field not in node or node[field] is None:
        node[field] = CommentedSeq()
    elif isinstance(node[field], str):
        node[field] = CommentedSeq([node[field]])
    return node[field]  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Apply a single mutation to the raw YAML data
# ---------------------------------------------------------------------------

def apply_mutation(data: CommentedMap, mutation: Mutation) -> str:
    """Apply *mutation* to *data* in place. Returns a human-readable confirmation."""

    if isinstance(mutation, TouchNode):
        _, created = _ensure_node(data, mutation.name)
        return f"created '{mutation.name}'" if created else f"'{mutation.name}' already exists"

    if isinstance(mutation, SetLabel):
        node, _ = _ensure_node(data, mutation.name)
        node["label"] = mutation.label
        return f"'{mutation.name}' label -> {mutation.label!r}"

    if isinstance(mutation, SetStatus):
        node, _ = _ensure_node(data, mutation.name)
        node["status"] = mutation.status
        return f"'{mutation.name}' status -> {mutation.status}"

    if isinstance(mutation, AddDep):
        node, _ = _ensure_node(data, mutation.name)
        deps = _list_field(node, "deps")
        if mutation.dep not in deps:
            deps.append(mutation.dep)
            return f"'{mutation.name}' -> '{mutation.dep}'"
        return f"'{mutation.name}' already depends on '{mutation.dep}'"

    if isinstance(mutation, AddBlocks):
        node, _ = _ensure_node(data, mutation.name)
        blocks = _list_field(node, "blocks")
        if mutation.blocked not in blocks:
            blocks.append(mutation.blocked)
            return f"'{mutation.name}' -| '{mutation.blocked}'"
        return f"'{mutation.name}' already blocks '{mutation.blocked}'"

    if isinstance(mutation, SetNote):
        node, _ = _ensure_node(data, mutation.name)
        node["note"] = mutation.note
        return f"'{mutation.name}' note set"

    return "no-op"


# ---------------------------------------------------------------------------
# Readline completion
# ---------------------------------------------------------------------------

def _build_completions(data: CommentedMap) -> List[str]:
    """Collect node short-names and :: paths for tab completion."""
    try:
        graph = load.__wrapped__(data) if hasattr(load, "__wrapped__") else None
    except Exception:
        graph = None

    names: List[str] = []
    nodes = data.get("nodes") or []
    for entry in nodes:
        if isinstance(entry, str):
            names.append(entry)
        elif isinstance(entry, CommentedMap):
            n = entry.get("name")
            if n:
                names.append(str(n))

    if graph is not None:
        try:
            reg = build_node_registry(graph)
            names.extend(reg.keys())
        except Exception:
            pass

    return sorted(set(names))


def _install_completer(completions: List[str]) -> None:
    try:
        import readline

        def completer(text: str, state: int) -> Optional[str]:
            matches = [c for c in completions if c.startswith(text)]
            return matches[state] if state < len(matches) else None

        readline.set_completer(completer)
        readline.set_completer_delims(" \t")
        readline.parse_and_bind(
            "bind ^I rl_complete" if sys.platform == "darwin" else "tab: complete"
        )
    except ImportError:
        pass  # readline not available (Windows) — degrade silently


# ---------------------------------------------------------------------------
# Interactive loop
# ---------------------------------------------------------------------------

_HELP = """\
Syntax:
  name              touch / create node
  name: Label       set label
  name active       set status  (active | done | todo | blocked)
  name -> dep       add dep edge
  name -| blocked   add blocks edge
  name "note"       set note

Tab completes node names. Empty line or ^D to save and quit.\
"""

_EXIT_WORDS = {"q", "quit", "exit", ":q"}


def run_scratch(path: Path) -> int:
    """Run the interactive scratch loop against *path*. Returns exit code."""
    if not path.exists():
        print(f"[scratch] ERROR: file not found: {path}", file=sys.stderr)
        return 1

    y, data = _load_raw(path)

    # Count existing nodes for the banner
    nodes_list = data.get("nodes") or []
    n_nodes = len(nodes_list)

    completions = _build_completions(data)
    _install_completer(completions)

    title = data.get("title") or data.get("name") or path.name
    print(f"[scratch] {title}  ({n_nodes} node{'s' if n_nodes != 1 else ''})")
    print(f"[scratch] {path}")
    print(_HELP)

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            break
        if line in _EXIT_WORDS:
            break
        if line in {"?", "help"}:
            print(_HELP)
            continue

        mutation = parse_line(line)
        if mutation is None:
            print(f"  ? unrecognised: {line!r}")
            continue

        msg = apply_mutation(data, mutation)
        _save_raw(y, data, path)

        # Refresh completions after each write
        completions.clear()
        completions.extend(_build_completions(data))
        _install_completer(completions)

        print(f"  {msg}")

    print("[scratch] saved.")
    return 0
