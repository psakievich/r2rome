"""
Tests for r2rome.html_writer — background theme toggle wiring.
"""

from pathlib import Path

from r2rome.html_writer import write_all_pages, write_page
from r2rome.model import THEMES


def _write_svg(tmp_path: Path, name: str = "index") -> Path:
    svg_path = tmp_path / f"{name}.svg"
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><g class="graph">'
        '<polygon fill="#0d0f14" points="0,0"/></g></svg>',
        encoding="utf-8",
    )
    return svg_path


class TestWritePageThemeToggle:
    def test_default_theme_is_dark(self, tmp_path):
        svg_path = _write_svg(tmp_path)
        out = tmp_path / "index.html"
        write_page(svg_path, out, title="T", breadcrumb=[{"label": "T"}])
        html = out.read_text()
        assert 'data-theme="dark"' in html

    def test_color_scheme_sets_initial_theme(self, tmp_path):
        svg_path = _write_svg(tmp_path)
        out = tmp_path / "index.html"
        write_page(
            svg_path, out, title="T", breadcrumb=[{"label": "T"}],
            color_scheme="light",
        )
        html = out.read_text()
        assert 'data-theme="light"' in html

    def test_unknown_color_scheme_falls_back_to_dark(self, tmp_path):
        svg_path = _write_svg(tmp_path)
        out = tmp_path / "index.html"
        write_page(
            svg_path, out, title="T", breadcrumb=[{"label": "T"}],
            color_scheme="solarized",
        )
        html = out.read_text()
        assert 'data-theme="dark"' in html

    def test_canvas_bg_values_present_for_both_themes(self, tmp_path):
        svg_path = _write_svg(tmp_path)
        out = tmp_path / "index.html"
        write_page(svg_path, out, title="T", breadcrumb=[{"label": "T"}])
        html = out.read_text()
        assert THEMES["dark"]["graph_attr"]["bgcolor"] in html
        assert THEMES["light"]["graph_attr"]["bgcolor"] in html

    def test_toggle_button_present(self, tmp_path):
        svg_path = _write_svg(tmp_path)
        out = tmp_path / "index.html"
        write_page(svg_path, out, title="T", breadcrumb=[{"label": "T"}])
        html = out.read_text()
        assert 'id="btn-theme-toggle"' in html

    def test_cdn_page_also_carries_theme_toggle(self, tmp_path):
        svg_path = _write_svg(tmp_path)
        out = tmp_path / "index.html"
        write_page(
            svg_path, out, title="T", breadcrumb=[{"label": "T"}],
            cdn=True, graph_data={"nodes": [], "edges": []},
            color_scheme="light",
        )
        html = out.read_text()
        assert 'data-theme="light"' in html
        assert 'id="btn-theme-toggle"' in html


class TestWriteAllPagesColorScheme:
    def test_color_scheme_threaded_from_level_map(self, tmp_path):
        svg_dir = tmp_path / "svg"
        svg_dir.mkdir()
        _write_svg(svg_dir, "index")

        level_map = [{
            "name": "root", "title": "Root", "parent": None,
            "children": [], "color_scheme": "light",
        }]
        out_dir = tmp_path / "out"
        write_all_pages(svg_dir=svg_dir, output_dir=out_dir, level_map=level_map)
        html = (out_dir / "index.html").read_text()
        assert 'data-theme="light"' in html

    def test_missing_color_scheme_defaults_to_dark(self, tmp_path):
        svg_dir = tmp_path / "svg"
        svg_dir.mkdir()
        _write_svg(svg_dir, "index")

        level_map = [{"name": "root", "title": "Root", "parent": None, "children": []}]
        out_dir = tmp_path / "out"
        write_all_pages(svg_dir=svg_dir, output_dir=out_dir, level_map=level_map)
        html = (out_dir / "index.html").read_text()
        assert 'data-theme="dark"' in html
