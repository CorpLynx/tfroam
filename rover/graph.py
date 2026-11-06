"""Graph nodes and edges builder (port of ``graph.go``).

Go iterates maps in random order, so node and edge *order* in its output varies
between runs. This port iterates in sorted order for reproducible output; the
content of every node and edge is the same.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .map import (
    RESOURCE_TYPE_FILE,
    RESOURCE_TYPE_LOCAL,
    RESOURCE_TYPE_VARIABLE,
    Map,
    MapResource,
)
from .plan import Expression
from .rso import (
    RESOURCE_TYPE_DATA,
    RESOURCE_TYPE_MODULE,
    RESOURCE_TYPE_OUTPUT,
    RESOURCE_TYPE_RESOURCE,
    ResourcesOverview,
)

log = logging.getLogger("rover")

VARIABLE_COLOR = "#1d7ada"
OUTPUT_COLOR = "#ffc107"
DATA_COLOR = "#dc477d"
MODULE_COLOR = "#8450ba"
MODULE_BG_COLOR = "white"
FNAME_BG_COLOR = "white"
RESOURCE_COLOR = "lightgray"
LOCAL_COLOR = "black"

_BRACKETS = re.compile(r"\[[^\[\]]*\]")


@dataclass
class NodeData:
    id: str = ""
    label: str = ""
    type: str = ""
    parent: str = ""
    parent_color: str = ""
    change: str = ""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id}
        if self.label:
            out["label"] = self.label
        if self.type:
            out["type"] = self.type
        if self.parent:
            out["parent"] = self.parent
        if self.parent_color:
            out["parentColor"] = self.parent_color
        if self.change:
            out["change"] = self.change
        return out


@dataclass
class Node:
    data: NodeData = field(default_factory=NodeData)
    classes: str = ""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"data": self.data.to_json()}
        if self.classes:
            out["classes"] = self.classes
        return out


@dataclass
class EdgeData:
    id: str
    source: str
    target: str
    gradient: str = ""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "source": self.source, "target": self.target}
        if self.gradient:
            out["gradient"] = self.gradient
        return out


@dataclass
class Edge:
    data: EdgeData
    classes: str = ""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"data": self.data.to_json()}
        if self.classes:
            out["classes"] = self.classes
        return out


@dataclass
class Graph:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"nodes": [n.to_json() for n in self.nodes], "edges": [e.to_json() for e in self.edges]}


_ZERO_NODE = Node()


def get_resource_color(resource_type: str) -> str:
    return {
        RESOURCE_TYPE_MODULE: MODULE_COLOR,
        RESOURCE_TYPE_DATA: DATA_COLOR,
        RESOURCE_TYPE_OUTPUT: OUTPUT_COLOR,
        RESOURCE_TYPE_VARIABLE: VARIABLE_COLOR,
        RESOURCE_TYPE_LOCAL: LOCAL_COLOR,
    }.get(resource_type, RESOURCE_COLOR)


def get_resource_class(resource_type: str) -> str:
    return {
        RESOURCE_TYPE_DATA: "data-type",
        RESOURCE_TYPE_OUTPUT: "output",
        RESOURCE_TYPE_VARIABLE: "variable",
        RESOURCE_TYPE_FILE: "fname",
        RESOURCE_TYPE_LOCAL: "locals",
        RESOURCE_TYPE_MODULE: "module",
    }.get(resource_type, "resource-type")


def _remove_suffix(text: str, suffix: str) -> str:
    """Go ``strings.TrimSuffix``: an empty suffix leaves the text alone."""
    return text[: -len(suffix)] if suffix and text.endswith(suffix) else text


def _add_nodes(base: str, parent: str, node_map: dict[str, Node], resources: dict[str, MapResource]) -> list[str]:
    nmo: list[str] = []

    for rid in sorted(resources):
        re_ = resources[rid]
        parent_node = node_map.get(parent, _ZERO_NODE)

        if re_.type in (RESOURCE_TYPE_RESOURCE, RESOURCE_TYPE_DATA):
            pid = parent
            if parent_node.data.type == RESOURCE_TYPE_FILE:
                pid = _remove_suffix(pid, parent_node.data.label)
                pid = _remove_suffix(pid, ".")

            mid = f"{pid}.{re_.resource_type}"
            mid = mid.removeprefix(f"{base}.")
            mid = mid.removeprefix(".")
            mid = _remove_suffix(mid, ".")

            label = mid.split(".")[-1]

            mid_parent = parent
            if mid_parent == mid:
                mid_parent = node_map.get(mid_parent, _ZERO_NODE).data.parent

            if node_map.get(mid_parent, _ZERO_NODE).data.type == RESOURCE_TYPE_FILE:
                mid = f"{mid} {{{parent_node.data.label}}}"

            nmo.append(mid)
            node_map[mid] = Node(
                data=NodeData(
                    id=mid,
                    label=label,
                    type=re_.type,
                    parent=mid_parent,
                    parent_color=get_resource_color(parent_node.data.type),
                ),
                classes=f"{re_.type}-type",
            )

            change = re_.change_action
            nmo.append(rid)
            node_map[rid] = Node(
                data=NodeData(
                    id=rid,
                    label=re_.name,
                    type=re_.type,
                    parent=mid,
                    parent_color=get_resource_color(parent_node.data.type),
                    change=change,
                ),
                classes=f"{re_.type}-name {change}",
            )

            nmo.extend(_add_nodes(base, rid, node_map, re_.children))

        elif re_.type == RESOURCE_TYPE_FILE:
            fid = rid if parent == base else f"{parent}.{rid}"
            nmo.append(fid)
            node_map[fid] = Node(
                data=NodeData(
                    id=fid,
                    label=rid,
                    type=re_.type,
                    parent=parent,
                    parent_color=get_resource_color(parent_node.data.type),
                ),
                classes=get_resource_class(re_.type),
            )
            nmo.extend(_add_nodes(base, fid, node_map, re_.children))

        else:
            pid = parent
            if parent_node.data.type == RESOURCE_TYPE_FILE:
                pid = _remove_suffix(pid, parent_node.data.label)
                pid = _remove_suffix(pid, ".")

            nmo.append(rid)
            node_map[rid] = Node(
                data=NodeData(
                    id=rid,
                    label=rid.split(".")[-1],
                    type=re_.type,
                    parent=parent,
                    parent_color=get_resource_color(node_map.get(pid, _ZERO_NODE).data.type),
                ),
                classes=get_resource_class(re_.type),
            )
            nmo.extend(_add_nodes(base, rid, node_map, re_.children))

    return nmo


def generate_nodes(map_obj: Map) -> list[Node]:
    node_map: dict[str, Node] = {}
    base_path = map_obj.path.replace("./", "")

    nmo = [base_path]
    node_map[base_path] = Node(data=NodeData(id=base_path, label=base_path, type="basename"), classes="basename")
    nmo.extend(_add_nodes(base_path, base_path, node_map, map_obj.root))

    nodes: list[Node] = []
    seen: set[str] = set()
    for nid in nmo:
        if nid not in seen:
            nodes.append(node_map[nid])
            seen.add(nid)
    return nodes


def _add_edges(
    rso: ResourcesOverview, parent: str, edge_map: dict[str, Edge], resources: dict[str, MapResource]
) -> list[str]:
    emo: list[str] = []

    for rid in sorted(resources):
        re_ = resources[rid]
        config_id = _BRACKETS.sub("", rid)

        expressions: list[Expression | None] = []
        entry = rso.configs.get(config_id)
        if entry is not None:
            if entry.resource_config is not None:
                expressions = list(entry.resource_config.expressions.values())
            elif entry.module_config is not None:
                expressions = list(entry.module_config.expressions.values())
            elif entry.output_config is not None:
                expressions = [entry.output_config.expression]

        for expression in expressions:
            if expression is None:  # Go would panic on a nil expression
                continue
            for ref in expression.references:
                if ref.startswith("each."):
                    continue

                source_color = get_resource_color(re_.type)
                target_id = f"{parent}.{ref}" if parent else ref

                target_color = RESOURCE_COLOR
                if "output." in ref:
                    target_color = OUTPUT_COLOR
                elif "var." in ref:
                    target_color = VARIABLE_COLOR
                elif ref.startswith("module."):
                    target_color = MODULE_COLOR
                elif "data." in ref:
                    target_color = DATA_COLOR
                elif "local." in ref:
                    target_color = LOCAL_COLOR

                # Terraform 1.0 references point at attributes: keep only whole-resource references.
                parts = len(ref.split("."))
                if target_color == RESOURCE_COLOR and parts != 2:
                    continue
                if target_color == DATA_COLOR and parts != 3:
                    continue

                edge_id = f"{rid}->{target_id}"
                emo.append(edge_id)
                edge_map[edge_id] = Edge(
                    data=EdgeData(
                        id=edge_id,
                        source=rid,
                        target=target_id,
                        gradient=f"{source_color} {target_color}",
                    ),
                    classes="edge",
                )

        # Files are transparent for edge generation.
        child_parent = parent if re_.type == RESOURCE_TYPE_FILE else rid
        emo.extend(_add_edges(rso, child_parent, edge_map, re_.children))

    return emo


def generate_edges(rso: ResourcesOverview, map_obj: Map) -> list[Edge]:
    edge_map: dict[str, Edge] = {}
    emo = _add_edges(rso, "", edge_map, map_obj.root)

    edges: list[Edge] = []
    seen: set[str] = set()
    for eid in emo:
        if eid not in seen:
            edges.append(edge_map[eid])
            seen.add(eid)
    return edges


def generate_graph(rso: ResourcesOverview, map_obj: Map) -> Graph:
    log.info("Generating resource graph...")

    nodes = generate_nodes(map_obj)
    edges = generate_edges(rso, map_obj)

    # Edge case for terraform.workspace
    if any("terraform.workspace" in e.data.id for e in edges):
        nodes.append(
            Node(
                data=NodeData(
                    id="terraform.workspace",
                    label="terraform.workspace",
                    type="locals",
                    parent=map_obj.path.replace("./", ""),  # equal to the base path
                ),
                classes="locals",
            )
        )

    return Graph(nodes=nodes, edges=edges)
