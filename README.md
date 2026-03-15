# r2rome

> *The road to Rome wasn't built in a day.*

Visualize nested project complexity as navigable digraphs. Define your project
in YAML, render it to SVG or interactive HTML, and share focused views with
different stakeholders — from high-level milestones down to individual tasks.

## Features

- **Nested graphs** — epics contain subgraphs, which contain subgraphs. No depth limit.
- **Depth control** — render only N levels deep; deeper nodes become hyperlinked collapsed nodes.
- **`deps` and `blocks`** — declare edges inline on nodes. `deps` for dependencies, `blocks` for blocking relationships (rendered as dashed red edges).
- **Offline by default** — generates static SVG + HTML with zero external requests.
- **CDN mode** (`--cdn`) — interactive pan/zoom viewer using dagre-d3 from cdnjs.
- **DOT export** — emit raw DOT language for use with any Graphviz tooling.
- **Graceful degradation** — `r2rome dot` works without the `dot` binary installed.

## Installation

```bash
pip install r2rome
```

Rendering to SVG/PNG/PDF requires [Graphviz](https://graphviz.org) on your PATH:

```bash
# System
apt install graphviz
brew install graphviz

# Via Spack
spack install graphviz
spack load graphviz
```

DOT source generation (`r2rome dot`) works without Graphviz.

## Quick start

```bash
# Render to offline HTML (requires graphviz)
r2rome render project.yaml -o out/
open out/index.html

# Render only 2 levels deep; deeper nodes become links
r2rome render project.yaml -o out/ --depth 2

# Interactive CDN viewer (requires network when viewing)
r2rome render project.yaml -o out/ --cdn

# Emit DOT source (no graphviz binary needed)
r2rome dot project.yaml
r2rome dot project.yaml --level my_epic
r2rome dot project.yaml | dot -Tpng -o graph.png

# Show graph structure summary
r2rome info project.yaml
```

## Example

[Live preview — platform-rewrite.yaml](https://htmlpreview.github.io/?https://github.com/psakievich/r2rome/blob/main/examples/out/index.html)

The rendered output for [`examples/platform-rewrite.yaml`](examples/platform-rewrite.yaml) is committed to
[`examples/out/`](examples/out/) and kept up-to-date by CI.

## YAML schema

```yaml
name: root                    # required
title: My Project             # optional display title

nodes:
  - name: epic_one            # required, unique identifier
    label: Epic One           # optional display label
    status: active            # done | active | todo | blocked | deprecated
    note: "Free text note"
    deps: [epic_two]          # outgoing edges: epic_one -> epic_two
    blocks: [release_v1]      # blocking edges (dashed red): epic_one -> release_v1
    href: "./epic_one.html"   # optional hyperlink (passed through to DOT/SVG)
    # any other key/value pairs are forwarded as DOT node attributes

  - name: epic_two
    status: todo

graphs:                       # named subgraphs (recursive)
  - name: epic_one
    title: Epic One Details
    cluster: true             # default true; wraps in a DOT cluster box
    graph_attr:
      rankdir: TB             # override DOT graph attributes
    nodes:
      - name: task_a
        status: done
        deps: [task_b]
      - name: task_b
        status: active
    graphs:                   # nest further as needed
      - name: task_b
        nodes: [...]
```

### Editor support

A [JSON Schema](schemas/r2rome.schema.json) is included for tab completion and
validation in any editor that supports `yaml-language-server`.

**Per-file (any editor)** — add this comment to the top of your YAML file:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/psakievich/r2rome/main/schemas/r2rome.schema.json
```

Or use a relative path when working inside this repo:

```yaml
# yaml-language-server: $schema=../schemas/r2rome.schema.json
```

**VS Code** — install the [YAML extension](https://marketplace.visualstudio.com/items?itemName=redhat.vscode-yaml).
The included `.vscode/settings.json` auto-applies the schema to `examples/**/*.yaml`
and `*.r2rome.yaml` files without needing the comment.

**Neovim** — configure `yamlls` via `nvim-lspconfig`:

```lua
require("lspconfig").yamlls.setup({
  settings = {
    yaml = {
      schemas = {
        ["https://raw.githubusercontent.com/psakievich/r2rome/main/schemas/r2rome.schema.json"] = {
          "examples/*.yaml", "*.r2rome.yaml"
        }
      }
    }
  }
})
```

## Development

```bash
git clone https://github.com/psakievich/r2rome
cd r2rome
pip install -e ".[dev]"
pytest
```

## License

MIT
