# CLAUDE.md — r2rome

This file captures the soul, motivation, and design intent of r2rome for use
in Claude Code sessions. Before making any significant changes to this project,
read this file in full. When in doubt about a design decision, refer back to
the core motivations in section 1.

---

## 1. What r2rome is — and isn't

r2rome is a **thinking environment**, not a project management system.

The distinction is fundamental. Project management tools (Jira, GitHub Projects,
GitLab Issues, Microsoft Planner) assume you already know what the work is and
need to track it. r2rome assumes you are still figuring out what the work is,
and need a way to think — and think in public when needed.

The name comes from the adage: *the road to Rome wasn't built in a day.* Large,
complex efforts are not linear. They branch, they prune, they circle back. r2rome
is a tool for navigating that process without losing the thread.

**r2rome is not:**
- A replacement for Jira, GitHub Issues, or any ticket system
- A Gantt chart or timeline tool
- A database of tasks
- A team collaboration platform (though it can support collaboration)

**r2rome is:**
- A personal and shareable map of a project's complexity at any point in time
- A way to capture where you've been, where you are, and where you're going
- A rendering layer over a simple, human-editable YAML file
- A tool that operates at the speed of thought, not the speed of a GUI

---

## 2. The three core motivations

These were articulated by the author and should be treated as requirements,
not preferences.

### 2.1 Discovery

Planning and executing complex work is messy. Ideas branch. Plans reveal
problems with themselves. What looks like the critical path at the start often
isn't by the end.

r2rome supports this by letting any node become a full subgraph. You start with
a high-level idea, and as you discover its structure, you expand nodes into
graphs. As things get dropped or de-prioritized, you mark them (deprecated,
blocked) rather than delete them — or simply remove them and let git history
preserve what was tried.

The visual metaphor is deliberate: a digraph shows *direction* and
*dependency*, which is exactly what a branching discovery process has. The
graph is simultaneously:
- A map of where you've been (completed/deprecated nodes)
- A map of where you are (active nodes and their blockers)
- A map of where you're going (todo nodes and their relationships)

**The coloring system exists to make this visible at a glance.** Status colors
(done, active, todo, blocked, deprecated) and impact propagation
(`color_by_impact`) are not decorative — they are the primary way the tool
communicates the state of a project.

### 2.2 Communication

The author received repeated peer feedback that others could not see his vision
for complex projects — even when they agreed on the current state and the
desired end state, the path between them was invisible.

r2rome exists in part to make that path visible. The graph is a shareable
artifact that can be walked through live, exported as an image for a slide,
or sent as an HTML file for someone to explore independently.

Key requirements from this motivation:
- The tool must produce output that is immediately legible to someone who has
  never used r2rome
- Navigation must be self-explanatory (breadcrumbs, back links, clear hierarchy)
- The same project file must support multiple export formats for different
  communication contexts (image for slides, HTML for live walkthrough, DOT for
  technical audiences)
- The web GUI should support collaborative editing — when planning with someone
  who wants to dive into details, they should be able to point at things and
  suggest changes that get reflected in the YAML

### 2.3 Roll-up

Complex projects have stakeholders at different levels of abstraction. Some
want every detail of a small subsystem. Some only want the top-level view.
Some will start at the top and keep asking "why?" until they reach the leaves.

r2rome must serve all three without friction. The depth control (`--depth N`)
and per-level HTML navigation exist specifically for this. A director gets
`index.html`. An engineer gets `backend.html`. A curious stakeholder can
navigate from the top down at their own pace.

**The roll-up requirement is why the hierarchy must be consistent and
complete.** Every node that has substructure must be reachable, and the path
back to the top must always be visible.

---

## 3. Design principles

These flow directly from the core motivations. Treat them as constraints.

### 3.1 The YAML file is the source of truth

The YAML files are pure metadata. They are independent of the application.
They must be:
- Human-editable without r2rome installed
- Readable without any tooling at all
- Stable across r2rome versions (schema changes must be backward compatible
  or explicitly versioned)
- Safe to version-control in git alongside or separately from any codebase
  they describe

The renderers (SVG, HTML, DOT) are *views* into the YAML. They are
disposable. The YAML is not.

### 3.2 Operate at the speed of thought

The tool must be fast to use. Adding a node, changing a status, or marking
something blocked should take seconds in a text editor. The CLI should
require minimal flags for common operations. Output should be immediate.

This is why the tool is YAML-first and CLI-first. Heavy GUIs, web dashboards,
and complex configuration are explicitly out of scope for the core tool.

### 3.3 The CLI must be composable

The CLI should follow Unix conventions: commands that read from files and
write to stdout/files, with minimal required arguments. This is not just
style — it is a prerequisite for future vim/neovim integration and potential
scripting/automation use cases.

```bash
# These patterns must always work:
r2rome dot project.yaml | dot -Tpng -o out.png
r2rome dot project.yaml --level epic_one > epic.gv
r2rome render project.yaml -o out/
```

### 3.4 Graceful degradation is a feature

r2rome must be useful even when optional dependencies are missing. Specifically:
- `r2rome dot` must work without the `dot` binary (Graphviz)
- The tool must install cleanly in environments where Graphviz is not yet
  available (it will be installed via Spack on HPC systems)
- Missing optional dependencies should produce clear, actionable error messages
  that include Spack install instructions

### 3.5 Nodes are first-class citizens, edges are derived

Edges in r2rome are declared on nodes (`deps`, `blocks`), not in a separate
edge list. This is a deliberate schema decision — it keeps the YAML readable
and co-locates the relationship with the thing being related. Do not introduce
a top-level `edges:` list unless there is a compelling reason that cannot be
solved at the node level.

### 3.6 The visual language must be consistent

Status colors and node shapes carry meaning. Do not change them arbitrarily.
The current mapping:

| Status     | Meaning                          |
|------------|----------------------------------|
| active     | Being worked on now              |
| todo       | Planned, not started             |
| done       | Complete                         |
| blocked    | Cannot proceed, waiting on something |
| deprecated | Tried, discarded, kept for history |

Impact propagation (`color_by_impact`) colors nodes by their relationship to
a change set — red for direct impact, orange for transitive. This is general
enough to apply to CI pipelines, blocking chains, or any dependency analysis.

---

## 4. Architecture overview

```
src/r2rome/
├── model.py        # YAML parsing, GraphNode dataclass, Graph dataclass
│                   # No graphviz dependency. Pure Python data model.
│
├── assemble.py     # Wires model objects into graphviz.Digraph
│                   # color_by_impact (formerly ci_coloring) lives here
│                   # build_digraph handles depth limiting and href injection
│
├── render.py       # Graphviz binary detection and invocation
│                   # Graceful degradation logic
│                   # to_dot_source() requires no binary
│
├── html_writer.py  # Wraps SVG files in navigable HTML pages
│                   # Two modes: offline (static SVG) and cdn (interactive)
│
├── cli.py          # argparse entry point, subcommands: render, dot, info
│
└── templates/
    ├── offline_page.html   # Static SVG wrapper with breadcrumb nav
    └── cdn_page.html       # Interactive dagre-d3 viewer (CDN-dependent)
```

**Key relationships:**
- `cli.py` depends on all other modules
- `html_writer.py` depends on `model.py` only (for type hints)
- `render.py` depends on `model.py` and `assemble.py`
- `assemble.py` depends on `model.py`
- `model.py` has no internal dependencies — it is the foundation

**The graphviz Python package** (`pip install graphviz`) is a thin wrapper
that generates DOT source and invokes the `dot` binary. It is a runtime
dependency. The `dot` binary itself is an optional runtime dependency —
see principle 3.4.

---

## 5. Current state (as of initial scaffold)

### Built and tested
- YAML parsing and validation (`model.py`)
- GraphNode dataclass with `deps`, `blocks`, status validation, DOT attr passthrough
- Graph dataclass with recursive subgraph support
- Edge wiring with warning (not error) on unknown references (`assemble.py`)
- `color_by_impact` (propagates through both `deps` and `blocks`)
- `build_digraph` with depth limiting and href injection for collapsed nodes
- Graphviz binary detection and graceful degradation (`render.py`)
- `to_dot_source()` — pure Python, no binary required
- Per-level SVG rendering (`render_all_levels`)
- HTML page generation, offline and CDN modes (`html_writer.py`)
- CLI: `render`, `dot`, `info` subcommands (`cli.py`)
- pytest suite: `test_model.py`, `test_assemble.py`, `test_render.py`
- GitHub Actions CI: lint + test on ubuntu + macos, Python 3.8–3.12
- Two Jinja2 templates: `offline_page.html`, `cdn_page.html`

### Stubbed / known gaps
- `color_by_impact` is implemented but the CLI does not yet expose it as a
  subcommand. It is callable from Python directly.
- `deprecated` status is defined in the design but not yet in `VALID_STATUSES`
  in `model.py` — add it before first real use.
- HTML web GUI editing (edit YAML from browser, re-render) — designed for but
  not implemented. The standalone `project-graph.html` tool (in the repo
  history) had this working with a JSON/YAML editor modal.
- CDN template renders from pre-processed graph JSON — the Python-to-JS data
  handoff in `html_writer.py` is stubbed; the graph JSON serialization needs
  to be implemented.
- `render_all_levels` does not yet generate the `level_map` needed by
  `write_all_pages` automatically — these need to be connected in `cli.py`.

### Not started
- `color_by_impact` CLI subcommand
- vim/neovim integration
- Cloud file sync (iCloud Drive / OneDrive)
- Phone/tablet/browser editing interface
- `r2rome edit` subcommand for CLI-driven node manipulation
- Multi-file projects (cross-file node references)

---

## 6. Future direction

These are stated goals from the author. Do not make architectural decisions
that close off these possibilities.

### 6.1 Strong CLI for editing

The goal is to manipulate the YAML file from the CLI without opening an editor:

```bash
r2rome add node epic_one --label "Epic One" --status active
r2rome add edge epic_one --deps infra --blocks release_v1
r2rome set status infra_sec blocked
r2rome move node task_a --to epic_two   # re-parent a node
r2rome deprecate epic_old               # mark as deprecated, keep in file
```

These commands should read and write the YAML file in place, preserving
comments and formatting where possible (use `ruamel.yaml` instead of `pyyaml`
when implementing this — it is round-trip safe).

### 6.2 vim/neovim integration

The CLI composability requirement (principle 3.3) is specifically designed
to support this. The envisioned integration:
- A vim command that runs `r2rome render %` on the current file and opens
  the output in a browser
- Possibly a floating window showing the current graph rendered as ASCII
  or as an image via an image-capable terminal (kitty, iTerm2)
- Bindings for common edit operations without leaving the editor

Do not design the CLI in a way that requires interactive TTY input — all
operations must be scriptable.

### 6.3 Cloud sync and cross-platform editing

YAML files stored in iCloud Drive (personal) or OneDrive (work) should be
editable from:
- CLI (`r2rome` on macOS/Linux)
- Browser (web GUI — see 6.4)
- Phone/tablet (via the browser interface or a future native app)

The YAML-as-source-of-truth principle (3.1) is what makes this possible.
The file format must remain simple enough that any YAML editor — including
a plain text editor on a phone — produces valid input.

### 6.4 Web GUI editing

The standalone `project-graph.html` tool (built before the Python package)
demonstrated in-browser YAML editing. This should eventually be integrated
into the rendered output — specifically, the CDN mode pages should allow:
- Editing node labels, status, and notes in-place
- Adding and removing edges
- Exporting the modified YAML back to disk

This is primarily useful for the **Communication** use case — planning
sessions with collaborators who want to suggest changes live.

### 6.5 `color_by_impact` as a CLI subcommand

```bash
r2rome impact project.yaml --from infra_sec
r2rome impact project.yaml --from infra_sec --render -o out/
```

Should render the graph with impact coloring applied from one or more
source nodes, showing what is directly affected (red) and transitively
affected (orange).

---

## 7. What to preserve — the project's soul

These things should not be changed without explicit discussion with the author.

**The YAML schema edge convention.** Edges are declared on nodes via `deps`
and `blocks`. There is no top-level `edges:` list. This is intentional.

**The file-is-the-brain principle.** The YAML file must remain the single
source of truth. Do not introduce a database, a lock file, or any derived
artifact that becomes required for the tool to function.

**The three-motivation structure.** Features should be evaluatable against
Discovery, Communication, and Roll-up. If a proposed feature does not serve
at least one of these, it probably does not belong in r2rome core.

**Composability over convenience.** When in tension, prefer a CLI that is
scriptable and pipeable over one that is ergonomic but opaque. The editor
integration and web GUI are additive layers, not replacements for the CLI.

**The original `process.py` lineage.** The `color_by_impact` function,
the `GraphNode` concept, and the `graphs` → `nodes` YAML nesting all trace
back to the author's original `software_graphs.py` and `process.py` in
his dotfiles. These are the author's own ideas. Generated code should
extend them, not replace them.

**Graceful degradation.** The tool must always be partially useful. A user
without Graphviz should still be able to generate DOT. A user without a
browser should still be able to read the YAML. Never make the full install
a prerequisite for basic functionality.

---

## 8. Development conventions

- **Python 3.8+** — required for HPC/Spack environments that may not have
  newer Python available
- **`src/` layout** — do not move source files out of `src/r2rome/`
- **No internal `edges:` lists** — see principle 3.5
- **Warn, don't error, on unknown edge references** — the graph may be
  partially defined while being actively edited
- **Tests mock the `dot` binary** — do not write tests that require
  Graphviz to be installed; use `patch("shutil.which", ...)` instead
- **`ruamel.yaml` for round-trip editing** — use `pyyaml` for read-only
  parsing (current state), switch to `ruamel.yaml` when implementing
  in-place CLI editing (section 6.1) to preserve comments and formatting
- **Spack install hints** — any error message involving a missing binary
  or package should include a `spack install` command alongside the
  system package manager alternatives

---

*This document should be updated as the project evolves. When a future
direction item moves to current state, update sections 5 and 6 accordingly.
When a design principle is revised based on real usage, capture the reasoning
for the change here.*
