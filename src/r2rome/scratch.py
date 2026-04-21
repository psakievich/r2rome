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
  foo::bar             relative path — context::foo::bar
  ::foo::bar           one level up  — parent::foo::bar
  :::foo::bar          absolute path — foo::bar
  name::               dive into name (relative, prompt changes)
  ::name::             dive one level up then into name
  :::name::            dive to absolute name
  ::                   go up one level
  :::                  return to root

Tab completes node names (and :: paths) on all entries.
Empty line, ``q``, or Ctrl-D exits and saves.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Union

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from r2rome.model import VALID_STATUSES


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

Mutation = Union[TouchNode, SetLabel, SetStatus, AddDep, AddBlocks, SetNote]


# ---------------------------------------------------------------------------
# Line parser
# ---------------------------------------------------------------------------

_STATUS_RE  = re.compile(r'^(\S+)\s+(' + '|'.join(VALID_STATUSES) + r')$')
_LABEL_RE   = re.compile(r'^(\S+):\s+(.+)$')
_DEP_RE     = re.compile(r'^(\S+)\s+->\s+(\S+)$')
_BLOCKS_RE  = re.compile(r'^(\S+)\s+-\|\s+(\S+)$')
_NOTE_RE    = re.compile(r'^(\S+)\s+"(.+)"$')
_TOUCH_RE   = re.compile(r'^([\w:]+)$')
_CONTEXT_RE   = re.compile(r'^([\w]+(?:::[\w]+)*)::$')  # "epic::" or "a::b::" to dive in
_VALID_PATH_RE = re.compile(r'^[\w]+(?:::[\w]+)*$')    # valid :: path: no spaces, no leading ::


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


def _ensure_nested_node(data: CommentedMap, path: str) -> Tuple[CommentedMap, bool]:
    """Navigate a :: path and return (node_dict, created).

    'epic::task_b' finds 'epic' in data's nodes, ensures it has a graph block,
    then finds or creates 'task_b' in that graph's nodes list.
    Non-path names (no '::') delegate directly to _ensure_node.
    """
    if path.startswith(":::"):  # defensive: callers should pre-resolve, but strip sigil if not
        path = path[3:]
    parts = [p for p in path.split("::") if p]
    if len(parts) == 1:
        return _ensure_node(data, parts[0])

    current_data = data
    for part in parts[:-1]:
        parent_node, _ = _ensure_node(current_data, part)
        if "graph" not in parent_node or parent_node["graph"] is None:
            parent_node["graph"] = CommentedMap()
        graph_block = parent_node["graph"]
        if "nodes" not in graph_block or graph_block["nodes"] is None:
            graph_block["nodes"] = CommentedSeq()
        current_data = graph_block

    return _ensure_node(current_data, parts[-1])


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
        _, created = _ensure_nested_node(data, mutation.name)
        return f"created '{mutation.name}'" if created else f"'{mutation.name}' already exists"

    if isinstance(mutation, SetLabel):
        node, _ = _ensure_nested_node(data, mutation.name)
        node["label"] = mutation.label
        return f"'{mutation.name}' label -> {mutation.label!r}"

    if isinstance(mutation, SetStatus):
        node, _ = _ensure_nested_node(data, mutation.name)
        node["status"] = mutation.status
        return f"'{mutation.name}' status -> {mutation.status}"

    if isinstance(mutation, AddDep):
        node, _ = _ensure_nested_node(data, mutation.name)
        deps = _list_field(node, "deps")
        if mutation.dep not in deps:
            deps.append(mutation.dep)
            return f"'{mutation.name}' -> '{mutation.dep}'"
        return f"'{mutation.name}' already depends on '{mutation.dep}'"

    if isinstance(mutation, AddBlocks):
        node, _ = _ensure_nested_node(data, mutation.name)
        blocks = _list_field(node, "blocks")
        if mutation.blocked not in blocks:
            blocks.append(mutation.blocked)
            return f"'{mutation.name}' -| '{mutation.blocked}'"
        return f"'{mutation.name}' already blocks '{mutation.blocked}'"

    if isinstance(mutation, SetNote):
        node, _ = _ensure_nested_node(data, mutation.name)
        node["note"] = mutation.note
        return f"'{mutation.name}' note set"

    return "no-op"


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------

def _resolve_relative(name: str, context: str) -> str:
    """Resolve *name* relative to the current *context* path.

    Three-tier path model:
      :::foo::bar   absolute from root — strip ::: and use as-is
      ::sibling     one level up from context — parent::sibling
      foo::bar      relative to context — context::foo::bar
      bare          relative to context — context::bare

    At root (empty context), ::: and :: prefixes are both just stripped.
    """
    if name.startswith(":::"):
        return name[3:]  # absolute — drop the ::: sigil

    if not context:
        return name[2:] if name.startswith("::") else name

    if name.startswith("::"):
        rest = name[2:]
        parent = context.rsplit("::", 1)[0] if "::" in context else ""
        if not parent:
            return rest
        return f"{parent}::{rest}" if rest else parent

    return f"{context}::{name}"


def _apply_context(mutation: Mutation, context: str) -> Mutation:
    """Resolve all names in *mutation* relative to *context*.

    Subject name: always resolved (bare or :: path).
    Dep/block targets: only resolved when they contain '::' — bare target
    names are intentionally left as local refs within the subgraph.
    """
    def _subj(name: str) -> str:
        return _resolve_relative(name, context)

    def _tgt(name: str) -> str:
        return _resolve_relative(name, context) if "::" in name else name

    if isinstance(mutation, TouchNode):
        return TouchNode(_subj(mutation.name))
    if isinstance(mutation, SetLabel):
        return SetLabel(_subj(mutation.name), mutation.label)
    if isinstance(mutation, SetStatus):
        return SetStatus(_subj(mutation.name), mutation.status)
    if isinstance(mutation, AddDep):
        return AddDep(_subj(mutation.name), _tgt(mutation.dep))
    if isinstance(mutation, AddBlocks):
        return AddBlocks(_subj(mutation.name), _tgt(mutation.blocked))
    if isinstance(mutation, SetNote):
        return SetNote(_subj(mutation.name), mutation.note)
    return mutation


# ---------------------------------------------------------------------------
# Readline completion
# ---------------------------------------------------------------------------

def _collect_completions(data: CommentedMap, names: List[str], prefix: str = "") -> None:
    """Recursively collect node names (and :: paths for subgraph nodes)."""
    nodes = data.get("nodes") or []
    for entry in nodes:
        if isinstance(entry, str):
            short: Optional[str] = entry
        elif isinstance(entry, CommentedMap):
            short = entry.get("name")
        else:
            continue
        if not short:
            continue
        full = f"{prefix}{short}" if prefix else short
        names.append(full)
        if isinstance(entry, CommentedMap):
            graph_block = entry.get("graph")
            if graph_block and isinstance(graph_block, CommentedMap):
                _collect_completions(graph_block, names, prefix=f"{full}::")


def _build_completions(data: CommentedMap) -> List[str]:
    """Collect all node paths as absolute names (root-relative)."""
    names: List[str] = []
    _collect_completions(data, names)
    return sorted(set(names))


def _compute_completions(text: str, context: str, data: CommentedMap) -> List[str]:
    """Return TAB completions for *text* given the current *context*.

    Three-tier model mirrors path resolution:
      bare / relative text   →  children of current context (no prefix)
      :: prefix              →  children of parent context   (:: prefixed)
      ::: prefix             →  all absolute paths           (::: prefixed)

    At root (empty context) all absolute paths are offered directly.
    """
    all_names = _build_completions(data)

    if not context:
        return [n for n in all_names if n.startswith(text)]

    if text.startswith(":::"):
        stem = text[3:]
        return sorted(f":::{n}" for n in all_names if n.startswith(stem))

    if text.startswith("::"):
        stem = text[2:]
        if "::" in context:
            parent = context.rsplit("::", 1)[0]
            par_prefix = parent + "::"
            par_names = [n[len(par_prefix):] for n in all_names if n.startswith(par_prefix)]
        else:
            par_names = [n for n in all_names if "::" not in n]
        return sorted(f"::{n}" for n in par_names if n.startswith(stem))

    ctx_prefix = context + "::"
    current = [n[len(ctx_prefix):] for n in all_names if n.startswith(ctx_prefix)]
    return sorted(n for n in current if n.startswith(text))


def _install_live_completer(data: CommentedMap, context_ref: List[str]) -> None:
    """Install a readline completer that recomputes from live data on each TAB press.

    *context_ref* is a single-element list; update context_ref[0] to change
    context without re-registering the completer.  Completions are cached for
    the duration of one TAB press (state=0 recomputes, subsequent states reuse).
    """
    try:
        import readline
        _cache: List[str] = []

        def completer(text: str, state: int) -> Optional[str]:
            if state == 0:
                _cache.clear()
                _cache.extend(_compute_completions(text, context_ref[0], data))
            return _cache[state] if state < len(_cache) else None

        readline.set_completer(completer)
        readline.set_completer_delims(" \t")
        if "libedit" in getattr(readline, "__doc__", ""):
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")
    except Exception:
        pass  # degrade silently (Windows, broken readline, etc.)


# ---------------------------------------------------------------------------
# Interactive loop
# ---------------------------------------------------------------------------

_HELP = """\
Path prefixes (mutations and context dives):
  foo::bar          relative — resolves to context::foo::bar
  ::foo::bar        one level up — resolves to parent::foo::bar
  :::foo::bar       absolute from root — resolves to foo::bar

Syntax:
  name              touch / create node
  name: Label       set label
  name active       set status  (active | done | todo | blocked)
  name -> dep       add dep edge (bare dep = local ref; :: dep = resolved)
  name -| blocked   add blocks edge
  name "note"       set note

Context navigation (prompt changes to show current context):
  name::            dive in (relative)
  ::name::          dive to sibling/uncle (one level up)
  :::name::         dive to absolute path
  ::                go up one level
  :::               return to root

Tab completion scopes:
  <tab>             children of current context
  ::<tab>           children of parent context
  :::<tab>          absolute paths from root\
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

    context = ""
    context_ref = [context]  # single-element mutable list: closure-shared state for live completer
    _install_live_completer(data, context_ref)

    title = data.get("title") or data.get("name") or path.name
    print(f"[scratch] {title}  ({n_nodes} node{'s' if n_nodes != 1 else ''})")
    print(f"[scratch] {path}")
    print(_HELP)

    while True:
        prompt = f"{context}::> " if context else "> "
        try:
            line = input(prompt).strip()
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

        # ---------------------------------------------------------------
        # Context navigation
        # ---------------------------------------------------------------
        is_ctx_nav = False

        if line == ":::":
            context = ""
            is_ctx_nav = True
        elif line == "::":
            context = context.rsplit("::", 1)[0] if "::" in context else ""
            is_ctx_nav = True
        elif line.startswith(":::") and line.endswith("::") and line[3:-2]:
            # Absolute dive: :::foo:: or :::foo::bar::
            seg = line[3:-2]
            if not _VALID_PATH_RE.match(seg):
                print(f"  ? invalid path: {seg!r}")
                continue
            context = seg
            is_ctx_nav = True
        elif line.startswith("::") and line.endswith("::") and line[2:-2]:
            # One-level-up dive: ::baz:: or ::foo::bar::
            seg = line[2:-2]
            if not _VALID_PATH_RE.match(seg):
                print(f"  ? invalid path: {seg!r}")
                continue
            parent = context.rsplit("::", 1)[0] if "::" in context else ""
            context = f"{parent}::{seg}" if parent else seg
            is_ctx_nav = True
        else:
            m = _CONTEXT_RE.match(line)
            if m:
                # Relative dive: baz:: appends to current context
                segment = m.group(1)
                context = f"{context}::{segment}" if context else segment
                is_ctx_nav = True

        if is_ctx_nav:
            context_ref[0] = context
            label = f"'{context}::'" if context else "root"
            print(f"  [context] {label}")
            continue

        # ---------------------------------------------------------------
        # Mutations
        # ---------------------------------------------------------------
        mutation = parse_line(line)
        if mutation is None:
            print(f"  ? unrecognised: {line!r}")
            continue

        if context:
            mutation = _apply_context(mutation, context)

        msg = apply_mutation(data, mutation)
        _save_raw(y, data, path)
        print(f"  {msg}")

    print("[scratch] saved.")
    return 0
