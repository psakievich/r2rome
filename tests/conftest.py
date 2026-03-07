"""
Shared pytest fixtures for r2rome tests.
"""

import textwrap
from pathlib import Path

import pytest
import yaml

from r2rome.model import Graph, GraphNode, load


# ---------------------------------------------------------------------------
# Raw YAML fixtures
# ---------------------------------------------------------------------------

SIMPLE_YAML = textwrap.dedent("""\
    name: root
    title: Simple Test Project
    nodes:
      - name: a
        label: Node A
        status: active
        deps: [b, c]
      - name: b
        label: Node B
        status: done
      - name: c
        label: Node C
        status: todo
        blocks: [a]
""")

NESTED_YAML = textwrap.dedent("""\
    name: root
    title: Nested Project
    nodes:
      - name: epic_one
        label: Epic One
        status: active
        deps: [epic_two]
        graphs:
          - name: epic_one_detail
            nodes:
              - name: task_a
                status: done
              - name: task_b
                status: active
                deps: [task_a]
      - name: epic_two
        label: Epic Two
        status: todo
    graphs:
      - name: epic_one_detail
        nodes:
          - name: task_a
            status: done
          - name: task_b
            status: active
            deps: [task_a]
""")

INVALID_STATUS_YAML = textwrap.dedent("""\
    name: root
    nodes:
      - name: bad_node
        status: flying
""")

MISSING_NAME_YAML = textwrap.dedent("""\
    name: root
    nodes:
      - label: No Name Here
        status: active
""")


# ---------------------------------------------------------------------------
# File fixtures (written to tmp_path)
# ---------------------------------------------------------------------------

@pytest.fixture
def simple_yaml_file(tmp_path: Path) -> Path:
    f = tmp_path / "simple.yaml"
    f.write_text(SIMPLE_YAML)
    return f


@pytest.fixture
def nested_yaml_file(tmp_path: Path) -> Path:
    f = tmp_path / "nested.yaml"
    f.write_text(NESTED_YAML)
    return f


@pytest.fixture
def invalid_status_file(tmp_path: Path) -> Path:
    f = tmp_path / "invalid_status.yaml"
    f.write_text(INVALID_STATUS_YAML)
    return f


@pytest.fixture
def missing_name_file(tmp_path: Path) -> Path:
    f = tmp_path / "missing_name.yaml"
    f.write_text(MISSING_NAME_YAML)
    return f


# ---------------------------------------------------------------------------
# Pre-parsed graph fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def simple_graph(simple_yaml_file: Path) -> Graph:
    return load(simple_yaml_file)


@pytest.fixture
def nested_graph(nested_yaml_file: Path) -> Graph:
    return load(nested_yaml_file)


@pytest.fixture
def flat_nodes() -> list:
    """A flat list of GraphNodes mirroring the original software_graphs.py test."""
    return [
        GraphNode("a", deps=["b", "c", "d"]),
        GraphNode("b", deps=["e"]),
        GraphNode("c", deps=["e", "f"]),
        GraphNode("d", deps=["i"]),
        GraphNode("e", deps=["g"]),
        GraphNode("f", deps=["g", "h"]),
        GraphNode("g", deps=[]),
        GraphNode("h", deps=[]),
        GraphNode("i", deps=[]),
    ]
