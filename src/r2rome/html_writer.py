"""
r2rome.html_writer
~~~~~~~~~~~~~~~~~~
Wraps rendered SVG files in navigable HTML pages.

Offline mode (default):
    SVG files are embedded directly in the HTML as inline <svg> or referenced
    as <img src="...svg">.  No external requests.  The page includes minimal
    vanilla JS only for the back/forward breadcrumb — no CDN, no framework.

CDN mode (--cdn flag):
    The page loads dagre-d3 and d3 from cdnjs.cloudflare.com and renders the
    graph interactively (pan, zoom, drill-down) rather than as a static SVG.
    Requires network access.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

if TYPE_CHECKING:
    from r2rome.model import Graph

# Path to bundled templates inside the package
_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _get_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )


def _embed_svg(svg_path: Path) -> str:
    """Return SVG file contents for inline embedding."""
    return svg_path.read_text(encoding="utf-8")


def _b64_svg(svg_path: Path) -> str:
    """Return a data URI for an SVG (fallback when inline is too large)."""
    data = base64.b64encode(svg_path.read_bytes()).decode("ascii")
    return f"data:image/svg+xml;base64,{data}"


def graph_to_json_data(graph: "Graph") -> dict:
    """Serialize a Graph to the {nodes, edges} dict expected by cdn_page.html."""
    nodes: list = []
    edges: list = []

    def _collect(g: "Graph") -> None:
        for node in g.nodes:
            href = node.dot_attrs.get("href")
            if href is None and node.children is not None:
                href = f"{node.name}.html"
            nodes.append({
                "name":   node.name,
                "label":  node.label or node.name,
                "status": node.status or "todo",
                "href":   href,
            })
            for dep in node.deps:
                edges.append({"from": node.name, "to": dep, "blocks": False})
            for block in node.blocks:
                edges.append({"from": node.name, "to": block, "blocks": True})
        for sg in g.subgraphs:
            _collect(sg)

    _collect(graph)
    return {"nodes": nodes, "edges": edges}


def write_page(
    svg_path: Path,
    output_html: Path,
    title: str,
    breadcrumb: List[dict],
    parent_href: Optional[str] = None,
    cdn: bool = False,
    graph_data: Optional[dict] = None,
) -> None:
    """Write a single HTML page wrapping an SVG graph.

    Args:
        svg_path:     Path to the rendered .svg file.
        output_html:  Destination .html path.
        title:        Page / graph title.
        breadcrumb:   List of {'label': str, 'href': str} dicts, root-first.
                      The last entry is the current page (no href needed).
        parent_href:  Optional href for a 'back' link.
        cdn:          If True, use the interactive CDN-based viewer.
        graph_data:   Graph data dict passed to the CDN template as JSON.
    """
    env  = _get_env()
    tmpl = env.get_template("cdn_page.html" if cdn else "offline_page.html")

    ctx: dict = {
        "title":       title,
        "breadcrumb":  breadcrumb,
        "parent_href": parent_href,
        "cdn":         cdn,
    }

    ctx["svg_content"] = _embed_svg(svg_path)

    output_html.write_text(tmpl.render(**ctx), encoding="utf-8")


def write_all_pages(
    svg_dir: Path,
    output_dir: Path,
    level_map: List[dict],
    cdn: bool = False,
    graph_data_map: Optional[Dict[str, dict]] = None,
) -> List[Path]:
    """Write HTML pages for all rendered SVG files.

    Args:
        svg_dir:         Directory containing rendered .svg files.
        output_dir:      Directory to write .html files into.
        level_map:       List of level descriptors, each with keys:
                           name:       graph name (matches svg filename stem)
                           title:      display title
                           parent:     parent graph name or None (root)
                           children:   list of child graph names
        cdn:             Write CDN-powered interactive pages.
        graph_data_map:  Mapping of graph name -> graph_data dict (required for cdn=True).

    Returns:
        List of paths to written HTML files.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []

    # Build a lookup for breadcrumb generation
    by_name = {lvl["name"]: lvl for lvl in level_map}

    def _breadcrumb(name: str) -> List[dict]:
        crumbs = []
        current = by_name.get(name)
        while current:
            stem = "index" if current["name"] == "root" else current["name"]
            crumbs.insert(0, {"label": current["title"], "href": f"{stem}.html"})
            parent_name = current.get("parent")
            current = by_name.get(parent_name) if parent_name else None
        return crumbs

    for lvl in level_map:
        stem     = "index" if lvl["name"] == "root" else lvl["name"]
        svg_path = svg_dir / f"{stem}.svg"
        out_path = output_dir / f"{stem}.html"

        if not svg_path.exists():
            continue

        parent_name = lvl.get("parent")
        parent_stem = None
        if parent_name:
            parent_stem = "index" if parent_name == "root" else parent_name

        write_page(
            svg_path=svg_path,
            output_html=out_path,
            title=lvl["title"],
            breadcrumb=_breadcrumb(lvl["name"]),
            parent_href=f"{parent_stem}.html" if parent_stem else None,
            cdn=cdn,
            graph_data=(graph_data_map or {}).get(lvl["name"]),
        )
        written.append(out_path)

    return written
