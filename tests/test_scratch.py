"""
Tests for r2rome.scratch — line parser and YAML mutations.
"""

import pytest
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from r2rome.scratch import (
    AddBlocks,
    AddDep,
    SetLabel,
    SetNote,
    SetStatus,
    TouchNode,
    apply_mutation,
    parse_line,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _data(yaml_str: str) -> CommentedMap:
    y = YAML()
    return y.load(yaml_str)


def _empty() -> CommentedMap:
    return _data("name: root\nnodes: []\n")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class TestParseLine:
    def test_touch_bare_name(self):
        assert parse_line("auth") == TouchNode("auth")

    def test_touch_name_with_underscores(self):
        assert parse_line("be_auth") == TouchNode("be_auth")

    def test_touch_cross_graph_path(self):
        assert parse_line("root::epic::task_a") == TouchNode("root::epic::task_a")

    def test_set_label(self):
        assert parse_line("auth: Auth Service") == SetLabel("auth", "Auth Service")

    def test_set_label_strips_whitespace(self):
        assert parse_line("auth:   Auth Service  ") == SetLabel("auth", "Auth Service")

    def test_set_status_all_valid(self):
        for status in ("active", "done", "todo", "blocked"):
            assert parse_line(f"node {status}") == SetStatus("node", status)

    def test_add_dep(self):
        assert parse_line("fe_auth -> be_auth") == AddDep("fe_auth", "be_auth")

    def test_add_dep_cross_graph(self):
        assert parse_line("fe_auth -> backend::be_auth") == AddDep("fe_auth", "backend::be_auth")

    def test_add_blocks(self):
        assert parse_line("infra_sec -| db_replica") == AddBlocks("infra_sec", "db_replica")

    def test_set_note(self):
        assert parse_line('auth "needs JWT review"') == SetNote("auth", "needs JWT review")

    def test_empty_line_returns_none(self):
        assert parse_line("") is None
        assert parse_line("   ") is None

    def test_unrecognised_returns_none(self):
        assert parse_line("!!!") is None


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

class TestApplyMutation:
    def test_touch_creates_node(self):
        data = _empty()
        msg = apply_mutation(data, TouchNode("auth"))
        assert any(
            (isinstance(n, str) and n == "auth") or
            (hasattr(n, "get") and n.get("name") == "auth")
            for n in data["nodes"]
        )
        assert "created" in msg

    def test_touch_existing_node_no_duplicate(self):
        data = _data("name: root\nnodes:\n  - name: auth\n")
        apply_mutation(data, TouchNode("auth"))
        names = [
            n if isinstance(n, str) else n.get("name")
            for n in data["nodes"]
        ]
        assert names.count("auth") == 1

    def test_set_label(self):
        data = _empty()
        apply_mutation(data, TouchNode("auth"))
        apply_mutation(data, SetLabel("auth", "Auth Service"))
        node = next(n for n in data["nodes"] if hasattr(n, "get") and n.get("name") == "auth")
        assert node["label"] == "Auth Service"

    def test_set_status(self):
        data = _empty()
        apply_mutation(data, TouchNode("auth"))
        apply_mutation(data, SetStatus("auth", "active"))
        node = next(n for n in data["nodes"] if hasattr(n, "get") and n.get("name") == "auth")
        assert node["status"] == "active"

    def test_add_dep(self):
        data = _empty()
        apply_mutation(data, TouchNode("fe_auth"))
        apply_mutation(data, AddDep("fe_auth", "be_auth"))
        node = next(n for n in data["nodes"] if hasattr(n, "get") and n.get("name") == "fe_auth")
        assert "be_auth" in node["deps"]

    def test_add_dep_no_duplicate(self):
        data = _empty()
        apply_mutation(data, TouchNode("fe_auth"))
        apply_mutation(data, AddDep("fe_auth", "be_auth"))
        apply_mutation(data, AddDep("fe_auth", "be_auth"))
        node = next(n for n in data["nodes"] if hasattr(n, "get") and n.get("name") == "fe_auth")
        assert node["deps"].count("be_auth") == 1

    def test_add_blocks(self):
        data = _empty()
        apply_mutation(data, TouchNode("infra_sec"))
        apply_mutation(data, AddBlocks("infra_sec", "db_replica"))
        node = next(n for n in data["nodes"] if hasattr(n, "get") and n.get("name") == "infra_sec")
        assert "db_replica" in node["blocks"]

    def test_set_note(self):
        data = _empty()
        apply_mutation(data, TouchNode("auth"))
        apply_mutation(data, SetNote("auth", "needs JWT review"))
        node = next(n for n in data["nodes"] if hasattr(n, "get") and n.get("name") == "auth")
        assert node["note"] == "needs JWT review"

    def test_creates_node_implicitly(self):
        """Mutations on non-existent nodes create the node automatically."""
        data = _empty()
        apply_mutation(data, SetStatus("new_node", "active"))
        node = next(n for n in data["nodes"] if hasattr(n, "get") and n.get("name") == "new_node")
        assert node["status"] == "active"

    def test_string_shorthand_node_converted_on_mutation(self):
        """String shorthand entries are promoted to dicts when mutated."""
        data = _data("name: root\nnodes: [a, b, c]\n")
        apply_mutation(data, SetStatus("b", "done"))
        node = next(n for n in data["nodes"] if hasattr(n, "get") and n.get("name") == "b")
        assert node["status"] == "done"

    def test_round_trip_preserves_existing_content(self, tmp_path):
        """Saving after a mutation doesn't corrupt unrelated YAML content."""
        from io import StringIO
        from ruamel.yaml import YAML as RY
        from r2rome.scratch import _load_raw, _save_raw

        src = tmp_path / "proj.yaml"
        src.write_text(
            "name: root\ntitle: My Project\n# keep this comment\nnodes:\n  - name: a\n    status: done\n"
        )
        y, data = _load_raw(src)
        apply_mutation(data, AddDep("a", "b"))
        _save_raw(y, data, src)

        result = src.read_text()
        assert "My Project" in result
        assert "keep this comment" in result
        assert "status: done" in result
        assert "b" in result
