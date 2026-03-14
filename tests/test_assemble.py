"""
Tests for r2rome.assemble — edge wiring, ci_coloring, blocks support.
"""

import warnings

from graphviz import Digraph

from r2rome.model import GraphNode
from r2rome.assemble import (
    assemble,
    build_digraph,
    ci_coloring,
    clear_attrs,
    color_nodes,
)


class TestColorNodes:
    def test_colors_matching_nodes(self):
        nodes = [GraphNode("a"), GraphNode("b"), GraphNode("c")]
        colored = color_nodes(nodes, {"a", "c"}, "red")
        assert len(colored) == 2
        assert all(n.dot_attrs.get("color") == "red" for n in colored)

    def test_returns_only_changed_nodes(self):
        nodes = [GraphNode("a"), GraphNode("b")]
        colored = color_nodes(nodes, {"a"}, "red")
        assert len(colored) == 1
        assert colored[0].name == "a"

    def test_no_match_returns_empty(self):
        nodes = [GraphNode("a"), GraphNode("b")]
        colored = color_nodes(nodes, {"z"}, "red")
        assert colored == []


class TestCiColoring:
    def test_direct_change_colored_red(self, flat_nodes):
        ci_coloring(flat_nodes, {"g"})
        g_node = next(n for n in flat_nodes if n.name == "g")
        assert g_node.dot_attrs.get("color") == "red"

    def test_dependents_colored_orange(self, flat_nodes):
        # e depends on g, so coloring g should propagate orange to e
        ci_coloring(flat_nodes, {"g"})
        e_node = next(n for n in flat_nodes if n.name == "e")
        assert e_node.dot_attrs.get("color") == "orange"

    def test_transitive_propagation(self, flat_nodes):
        # g -> e -> b, a (transitively)
        ci_coloring(flat_nodes, {"g"})
        b_node = next(n for n in flat_nodes if n.name == "b")
        assert b_node.dot_attrs.get("color") == "orange"

    def test_unrelated_node_not_colored(self, flat_nodes):
        ci_coloring(flat_nodes, {"g"})
        # 'i' depends on nothing that depends on g
        i_node = next(n for n in flat_nodes if n.name == "i")
        assert "color" not in i_node.dot_attrs

    def test_blocks_propagates_too(self):
        """ci_coloring should propagate through blocks edges as well."""
        nodes = [
            GraphNode("a", deps=[], blocks=["b"]),
            GraphNode("b", deps=[]),
        ]
        ci_coloring(nodes, {"a"})
        b_node = next(n for n in nodes if n.name == "b")
        assert b_node.dot_attrs.get("color") == "orange"


class TestClearAttrs:
    def test_clears_dot_attrs(self):
        nodes = [GraphNode("a", dot_attrs={"color": "red"})]
        clear_attrs(nodes)
        assert nodes[0].dot_attrs == {}

    def test_does_not_affect_other_fields(self):
        nodes = [GraphNode("a", status="active", dot_attrs={"color": "red"})]
        clear_attrs(nodes)
        assert nodes[0].status == "active"


class TestAssemble:
    def test_nodes_added_to_digraph(self):
        nodes = [GraphNode("a"), GraphNode("b")]
        dot = Digraph()
        assemble(nodes, dot)
        src = dot.source
        assert '"a"' in src or "a" in src
        assert '"b"' in src or "b" in src

    def test_deps_edges_added(self):
        nodes = [GraphNode("a", deps=["b"]), GraphNode("b")]
        dot = Digraph()
        assemble(nodes, dot)
        assert "a" in dot.source
        assert "->" in dot.source

    def test_blocks_edges_added_with_style(self):
        nodes = [GraphNode("a", blocks=["b"]), GraphNode("b")]
        dot = Digraph()
        assemble(nodes, dot)
        src = dot.source
        # blocks edges should have dashed style
        assert "dashed" in src

    def test_unknown_dep_warns_not_raises(self):
        nodes = [GraphNode("a", deps=["nonexistent"])]
        dot = Digraph()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            assemble(nodes, dot)
        assert any("nonexistent" in str(warning.message) for warning in w)

    def test_unknown_block_warns_not_raises(self):
        nodes = [GraphNode("a", blocks=["ghost"])]
        dot = Digraph()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            assemble(nodes, dot)
        assert any("ghost" in str(warning.message) for warning in w)


class TestBuildDigraph:
    def test_returns_digraph(self, simple_graph):
        result = build_digraph(simple_graph)
        assert isinstance(result, Digraph)

    def test_source_contains_node_names(self, simple_graph):
        src = build_digraph(simple_graph).source
        assert "a" in src
        assert "b" in src

    def test_depth_zero_collapses_subgraphs(self, nested_graph):
        """At depth=0, subgraphs should not be recursed into."""
        src_unlimited = build_digraph(nested_graph).source
        src_depth0    = build_digraph(nested_graph, max_depth=0).source
        # Unlimited render has more content (subgraph nodes)
        assert len(src_unlimited) >= len(src_depth0)

    def test_collapsed_node_has_double_border(self, nested_graph):
        """Collapsed subgraph nodes should have peripheries=2."""
        src = build_digraph(nested_graph, max_depth=0).source
        assert "peripheries" in src

    def test_collapsed_node_has_href(self, nested_graph, tmp_path):
        """Collapsed nodes should get an href when output_dir is provided."""
        src = build_digraph(
            nested_graph,
            max_depth=0,
            output_dir=str(tmp_path),
        ).source
        assert "href" in src
