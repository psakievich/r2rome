"""
Tests for r2rome.model — YAML parsing, GraphNode, Graph, validation.
"""

import json

import pytest

from r2rome.model import Graph, GraphNode, load, _coerce_list


class TestGraphNode:
    def test_label_defaults_to_name(self):
        node = GraphNode(name="my_node")
        assert node.label == "my_node"

    def test_explicit_label(self):
        node = GraphNode(name="n", label="My Node")
        assert node.label == "My Node"

    def test_valid_statuses(self):
        for status in ("done", "active", "todo", "blocked"):
            node = GraphNode(name="n", status=status)
            assert node.status == status

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError, match="invalid status"):
            GraphNode(name="n", status="flying")

    def test_deps_default_empty(self):
        node = GraphNode(name="n")
        assert node.deps == []

    def test_blocks_default_empty(self):
        node = GraphNode(name="n")
        assert node.blocks == []

    def test_effective_dot_attrs_status_styling(self):
        node = GraphNode(name="n", status="blocked")
        attrs = node.effective_dot_attrs()
        assert attrs["color"] == "#f06a6a"
        assert attrs["fontcolor"] == "#f06a6a"

    def test_effective_dot_attrs_href_forces_blue(self):
        node = GraphNode(name="n", dot_attrs={"href": "foo.html"})
        attrs = node.effective_dot_attrs()
        assert attrs["fontcolor"] == "blue"

    def test_from_dict_basic(self):
        node = GraphNode.from_dict({"name": "x", "label": "X Node", "status": "done"})
        assert node.name == "x"
        assert node.label == "X Node"
        assert node.status == "done"

    def test_from_dict_deps_and_blocks(self):
        node = GraphNode.from_dict({
            "name": "x",
            "deps": ["y", "z"],
            "blocks": ["w"],
        })
        assert node.deps == ["y", "z"]
        assert node.blocks == ["w"]

    def test_from_dict_extra_keys_become_dot_attrs(self):
        node = GraphNode.from_dict({"name": "x", "shape": "diamond", "href": "x.html"})
        assert node.dot_attrs["shape"] == "diamond"
        assert node.dot_attrs["href"] == "x.html"

    def test_from_dict_missing_name_raises(self):
        with pytest.raises(ValueError, match="missing required field 'name'"):
            GraphNode.from_dict({"label": "No Name"})

    def test_from_dict_invalid_status_raises(self):
        with pytest.raises(ValueError, match="invalid status"):
            GraphNode.from_dict({"name": "x", "status": "unknown"})

    def test_from_dict_single_dep_string(self):
        """A bare string dep (not a list) should be coerced to a list."""
        node = GraphNode.from_dict({"name": "x", "deps": "y"})
        assert node.deps == ["y"]


class TestGraph:
    def test_title_defaults_to_name(self):
        g = Graph(name="my_graph")
        assert g.title == "my_graph"

    def test_dot_name_cluster(self):
        g = Graph(name="epic", cluster=True)
        assert g.dot_name == "cluster_epic"

    def test_dot_name_no_cluster(self):
        g = Graph(name="epic", cluster=False)
        assert g.dot_name == "epic"

    def test_from_dict_empty_graph(self):
        g = Graph.from_dict({"name": "root"})
        assert g.name == "root"
        assert g.nodes == []
        assert g.subgraphs == []

    def test_from_dict_with_nodes(self):
        g = Graph.from_dict({
            "name": "root",
            "nodes": [
                {"name": "a", "status": "active"},
                {"name": "b", "status": "done"},
            ],
        })
        assert len(g.nodes) == 2
        assert g.nodes[0].name == "a"

    def test_from_dict_nested_graphs(self):
        g = Graph.from_dict({
            "name": "root",
            "graphs": [
                {"name": "sub", "nodes": [{"name": "x"}]},
            ],
        })
        assert len(g.subgraphs) == 1
        assert g.subgraphs[0].name == "sub"

    def test_graph_attr_merged_with_defaults(self):
        g = Graph.from_dict({"name": "root", "graph_attr": {"rankdir": "TB"}})
        # Custom value overrides default
        assert g.graph_attr["rankdir"] == "TB"
        # Default values still present
        assert "bgcolor" in g.graph_attr

    def test_all_node_names(self):
        g = Graph.from_dict({
            "name": "root",
            "nodes": [{"name": "a"}, {"name": "b"}],
        })
        assert set(g.all_node_names()) == {"a", "b"}


class TestLoad:
    def test_load_simple(self, simple_yaml_file):
        g = load(simple_yaml_file)
        assert g.name == "root"
        assert g.title == "Simple Test Project"
        assert len(g.nodes) == 3

    def test_load_node_deps(self, simple_graph):
        a = next(n for n in simple_graph.nodes if n.name == "a")
        assert "b" in a.deps
        assert "c" in a.deps

    def test_load_node_blocks(self, simple_graph):
        c = next(n for n in simple_graph.nodes if n.name == "c")
        assert "a" in c.blocks

    def test_load_nested(self, nested_yaml_file):
        g = load(nested_yaml_file)
        assert len(g.subgraphs) >= 1

    def test_load_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load(tmp_path / "nonexistent.yaml")

    def test_load_invalid_status_raises(self, invalid_status_file):
        with pytest.raises(ValueError, match="invalid status"):
            load(invalid_status_file)

    def test_load_missing_node_name_raises(self, missing_name_file):
        with pytest.raises(ValueError, match="missing required field 'name'"):
            load(missing_name_file)

    def test_load_non_mapping_raises(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text("- just a list\n- not a mapping\n")
        with pytest.raises(ValueError, match="Expected a mapping"):
            load(f)

    def test_load_json_simple(self, tmp_path):
        data = {
            "name": "root",
            "title": "Simple Test Project",
            "nodes": [
                {"name": "a", "label": "Node A", "status": "active", "deps": ["b", "c"]},
                {"name": "b", "label": "Node B", "status": "done"},
                {"name": "c", "label": "Node C", "status": "todo", "blocks": ["a"]},
            ],
        }
        f = tmp_path / "simple.json"
        f.write_text(json.dumps(data))
        g = load(f)
        assert g.name == "root"
        assert g.title == "Simple Test Project"
        assert len(g.nodes) == 3

    def test_load_json_nested(self, tmp_path):
        data = {
            "name": "root",
            "graphs": [
                {"name": "sub", "nodes": [{"name": "x", "status": "done"}]},
            ],
        }
        f = tmp_path / "nested.json"
        f.write_text(json.dumps(data))
        g = load(f)
        assert len(g.subgraphs) == 1
        assert g.subgraphs[0].name == "sub"

    def test_load_json_invalid_raises(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{not valid json")
        with pytest.raises(json.JSONDecodeError):
            load(f)

    def test_load_json_produces_same_graph_as_yaml(self, simple_yaml_file, tmp_path):
        """JSON and YAML inputs with identical content produce equal Graph objects."""
        data = {
            "name": "root",
            "title": "Simple Test Project",
            "nodes": [
                {"name": "a", "label": "Node A", "status": "active", "deps": ["b", "c"]},
                {"name": "b", "label": "Node B", "status": "done"},
                {"name": "c", "label": "Node C", "status": "todo", "blocks": ["a"]},
            ],
        }
        f = tmp_path / "simple.json"
        f.write_text(json.dumps(data))
        g_json = load(f)
        g_yaml = load(simple_yaml_file)
        assert g_json.name == g_yaml.name
        assert g_json.title == g_yaml.title
        assert len(g_json.nodes) == len(g_yaml.nodes)
        for jn, yn in zip(g_json.nodes, g_yaml.nodes):
            assert jn.name == yn.name
            assert jn.label == yn.label
            assert jn.status == yn.status
            assert jn.deps == yn.deps
            assert jn.blocks == yn.blocks


class TestCoerceList:
    def test_none_returns_empty(self):
        assert _coerce_list(None, "ctx") == []

    def test_string_becomes_single_item_list(self):
        assert _coerce_list("foo", "ctx") == ["foo"]

    def test_list_passthrough(self):
        assert _coerce_list(["a", "b"], "ctx") == ["a", "b"]

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="expected a list or string"):
            _coerce_list(42, "ctx")
