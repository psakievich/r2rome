"""
Tests for r2rome.scratch — line parser and YAML mutations.
"""

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from r2rome.scratch import (
    AddBlocks,
    AddDep,
    SetLabel,
    SetNote,
    SetStatus,
    TouchNode,
    _CONTEXT_RE,
    _apply_context,
    _build_completions,
    _compute_completions,
    _resolve_relative,
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


# ---------------------------------------------------------------------------
# Subgraph :: path mutations
# ---------------------------------------------------------------------------

class TestSubgraphMutations:
    def test_touch_creates_child_in_subgraph(self):
        data = _empty()
        apply_mutation(data, TouchNode("epic::task_a"))
        epic = next(n for n in data["nodes"] if hasattr(n, "get") and n.get("name") == "epic")
        assert "graph" in epic
        child_names = [
            n if isinstance(n, str) else n.get("name")
            for n in epic["graph"]["nodes"]
        ]
        assert "task_a" in child_names

    def test_set_status_in_subgraph(self):
        data = _empty()
        apply_mutation(data, TouchNode("epic::task_a"))
        apply_mutation(data, SetStatus("epic::task_a", "active"))
        epic = next(n for n in data["nodes"] if hasattr(n, "get") and n.get("name") == "epic")
        task = next(n for n in epic["graph"]["nodes"] if hasattr(n, "get") and n.get("name") == "task_a")
        assert task["status"] == "active"

    def test_set_label_in_subgraph(self):
        data = _empty()
        apply_mutation(data, SetLabel("epic::task_a", "Task A"))
        epic = next(n for n in data["nodes"] if hasattr(n, "get") and n.get("name") == "epic")
        task = next(n for n in epic["graph"]["nodes"] if hasattr(n, "get") and n.get("name") == "task_a")
        assert task["label"] == "Task A"

    def test_add_dep_in_subgraph(self):
        data = _empty()
        apply_mutation(data, TouchNode("epic::task_a"))
        apply_mutation(data, AddDep("epic::task_a", "epic::task_b"))
        epic = next(n for n in data["nodes"] if hasattr(n, "get") and n.get("name") == "epic")
        task = next(n for n in epic["graph"]["nodes"] if hasattr(n, "get") and n.get("name") == "task_a")
        assert "epic::task_b" in task["deps"]

    def test_promotes_existing_node_to_parent(self):
        """Touching parent::child auto-creates parent node if absent."""
        data = _empty()
        msg = apply_mutation(data, TouchNode("new_epic::subtask"))
        assert "created" in msg
        names = [
            n if isinstance(n, str) else n.get("name")
            for n in data["nodes"]
        ]
        assert "new_epic" in names

    def test_deep_path_three_levels(self):
        data = _empty()
        apply_mutation(data, TouchNode("epic::sub::leaf"))
        epic = next(n for n in data["nodes"] if hasattr(n, "get") and n.get("name") == "epic")
        sub = next(n for n in epic["graph"]["nodes"] if hasattr(n, "get") and n.get("name") == "sub")
        leaf_names = [
            n if isinstance(n, str) else n.get("name")
            for n in sub["graph"]["nodes"]
        ]
        assert "leaf" in leaf_names

    def test_touch_existing_subgraph_node_no_duplicate(self):
        data = _empty()
        apply_mutation(data, TouchNode("epic::task_a"))
        apply_mutation(data, TouchNode("epic::task_a"))
        epic = next(n for n in data["nodes"] if hasattr(n, "get") and n.get("name") == "epic")
        names = [
            n if isinstance(n, str) else n.get("name")
            for n in epic["graph"]["nodes"]
        ]
        assert names.count("task_a") == 1


# ---------------------------------------------------------------------------
# Completion builder
# ---------------------------------------------------------------------------

class TestBuildCompletions:
    def test_top_level_nodes_included(self):
        data = _data("name: root\nnodes:\n  - name: auth\n  - name: api\n")
        completions = _build_completions(data)
        assert "auth" in completions
        assert "api" in completions

    def test_subgraph_nodes_included_with_path(self):
        data = _data(
            "name: root\nnodes:\n"
            "  - name: epic\n"
            "    graph:\n"
            "      nodes:\n"
            "        - name: task_a\n"
        )
        completions = _build_completions(data)
        assert "epic" in completions
        assert "epic::task_a" in completions

    def test_string_shorthand_nodes_included(self):
        data = _data("name: root\nnodes: [alpha, beta]\n")
        completions = _build_completions(data)
        assert "alpha" in completions
        assert "beta" in completions

    def test_completions_are_sorted_and_unique(self):
        data = _data("name: root\nnodes:\n  - name: b\n  - name: a\n")
        completions = _build_completions(data)
        assert completions == sorted(set(completions))


# ---------------------------------------------------------------------------
# Context switch (_CONTEXT_RE, _apply_context, _context_completions)
# ---------------------------------------------------------------------------

class TestContextRegex:
    def test_simple_dive(self):
        m = _CONTEXT_RE.match("epic_two::")
        assert m and m.group(1) == "epic_two"

    def test_nested_dive(self):
        m = _CONTEXT_RE.match("epic::sub::")
        assert m and m.group(1) == "epic::sub"

    def test_double_colon_does_not_match(self):
        # "::" is handled as "go up" directly in run_scratch, not via regex
        assert _CONTEXT_RE.match("::") is None

    def test_triple_colon_does_not_match(self):
        # ":::" is handled as "reset to root" directly in run_scratch
        assert _CONTEXT_RE.match(":::") is None

    def test_bare_name_does_not_match(self):
        assert _CONTEXT_RE.match("epic_two") is None

    def test_full_path_does_not_match(self):
        assert _CONTEXT_RE.match("epic::task_a") is None


class TestResolveRelative:
    def test_bare_name_prefixed(self):
        assert _resolve_relative("task_a", "epic") == "epic::task_a"

    def test_sub_path_relative(self):
        assert _resolve_relative("baz::doe", "foo::bar") == "foo::bar::baz::doe"

    def test_leading_colons_up_one_level(self):
        assert _resolve_relative("::sibling", "foo::bar::baz") == "foo::bar::sibling"

    def test_leading_colons_up_with_sub_path(self):
        assert _resolve_relative("::sib::child", "foo::bar::baz") == "foo::bar::sib::child"

    def test_leading_colons_from_single_level(self):
        assert _resolve_relative("::other", "epic") == "other"

    def test_triple_colon_absolute_in_context(self):
        assert _resolve_relative(":::foo::bar", "epic::sub") == "foo::bar"

    def test_triple_colon_absolute_at_root(self):
        assert _resolve_relative(":::foo::bar", "") == "foo::bar"

    def test_at_root_bare_name_unchanged(self):
        assert _resolve_relative("task_a", "") == "task_a"

    def test_at_root_leading_colons_stripped(self):
        assert _resolve_relative("::node", "") == "node"

    def test_at_root_sub_path_unchanged(self):
        assert _resolve_relative("foo::bar", "") == "foo::bar"


class TestApplyContext:
    def test_bare_name_prefixed(self):
        mut = _apply_context(TouchNode("task_a"), "epic")
        assert mut == TouchNode("epic::task_a")

    def test_relative_sub_path_prefixed(self):
        mut = _apply_context(TouchNode("baz::doe"), "foo::bar")
        assert mut == TouchNode("foo::bar::baz::doe")

    def test_leading_colons_goes_up_one_level(self):
        mut = _apply_context(TouchNode("::sibling"), "foo::bar::baz")
        assert mut == TouchNode("foo::bar::sibling")

    def test_triple_colon_absolute(self):
        mut = _apply_context(TouchNode(":::root_node"), "foo::bar")
        assert mut == TouchNode("root_node")

    def test_set_label_prefixes_name(self):
        mut = _apply_context(SetLabel("task_a", "Task A"), "epic")
        assert mut == SetLabel("epic::task_a", "Task A")

    def test_set_status_prefixes_name(self):
        mut = _apply_context(SetStatus("task_a", "active"), "epic")
        assert mut == SetStatus("epic::task_a", "active")

    def test_add_dep_bare_target_stays_local(self):
        mut = _apply_context(AddDep("task_a", "task_b"), "epic")
        assert mut == AddDep("epic::task_a", "task_b")

    def test_add_dep_path_target_resolved_relative(self):
        mut = _apply_context(AddDep("task_a", "baz::doe"), "foo::bar")
        assert mut == AddDep("foo::bar::task_a", "foo::bar::baz::doe")

    def test_add_dep_absolute_target(self):
        mut = _apply_context(AddDep("task_a", ":::other::dep"), "foo::bar")
        assert mut == AddDep("foo::bar::task_a", "other::dep")

    def test_add_dep_leading_colon_target(self):
        mut = _apply_context(AddDep("task_a", "::other"), "foo::bar::baz")
        assert mut == AddDep("foo::bar::baz::task_a", "foo::bar::other")

    def test_add_blocks_bare_target_stays_local(self):
        mut = _apply_context(AddBlocks("task_a", "task_b"), "epic")
        assert mut == AddBlocks("epic::task_a", "task_b")

    def test_set_note_prefixes_name(self):
        mut = _apply_context(SetNote("task_a", "some note"), "epic")
        assert mut == SetNote("epic::task_a", "some note")


class TestComputeCompletions:
    def _data(self):
        return _data(
            "name: root\nnodes:\n"
            "  - name: epic\n"
            "    graph:\n"
            "      nodes:\n"
            "        - name: task_a\n"
            "        - name: task_b\n"
            "  - name: other\n"
        )

    def test_root_context_bare_text_returns_all(self):
        d = self._data()
        c = _compute_completions("", "", d)
        assert "epic" in c
        assert "epic::task_a" in c
        assert "other" in c

    def test_in_context_bare_text_shows_relative_only(self):
        d = self._data()
        c = _compute_completions("", "epic", d)
        assert "task_a" in c
        assert "task_b" in c
        # other nodes NOT shown by default in context
        assert "other" not in c
        assert "epic" not in c

    def test_in_context_double_colon_shows_parent_level(self):
        d = self._data()
        c = _compute_completions("::", "epic", d)
        # parent of "epic" is root — root nodes with :: prefix
        assert "::epic" in c
        assert "::other" in c

    def test_in_context_double_colon_prefix_filtered(self):
        d = self._data()
        c = _compute_completions("::e", "epic", d)
        assert "::epic" in c
        assert "::other" not in c

    def test_in_context_triple_colon_shows_absolute_paths(self):
        d = self._data()
        c = _compute_completions(":::", "epic", d)
        assert ":::epic" in c
        assert ":::other" in c
        assert ":::epic::task_a" in c

    def test_in_nested_context_double_colon_shows_grandparent_children(self):
        d = self._data()
        # inside epic::task_a, :: shows siblings (epic's children)
        c = _compute_completions("::", "epic::task_a", d)
        # parent of epic::task_a is epic — so children of epic
        assert "::task_a" in c
        assert "::task_b" in c
        # epic's parent (root) nodes should NOT appear
        assert "::other" not in c
