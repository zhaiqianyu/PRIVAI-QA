from __future__ import annotations

from collections import deque
from typing import Any

from src.knowledge.scitoolkg import build_reverse_graph, get_scitoolkg_tools, load_scitoolkg_graph

from .base import GraphAdapter


class SciToolKGGraphAdapter(GraphAdapter):
    """SciToolKG graph adapter backed by SciToolAgent persisted graph_store.json."""

    def __init__(self):
        self._graph = load_scitoolkg_graph()
        self._reverse = build_reverse_graph()

    async def query_nodes(self, keyword: str, **kwargs) -> dict[str, Any]:
        max_depth = int(kwargs.get("max_depth", 2))
        max_nodes = int(kwargs.get("max_nodes", 100))

        keyword = (keyword or "*").strip()
        if keyword == "*" or keyword == "":
            seed_nodes = [t.name for t in get_scitoolkg_tools()[: max_nodes]]
        else:
            lowered = keyword.lower()
            exact = [n for n in self._graph.keys() if n.lower() == lowered]
            if exact:
                seed_nodes = exact
            else:
                seed_nodes = [n for n in self._graph.keys() if lowered in n.lower()][:5]
                if not seed_nodes:
                    seed_nodes = [
                        n
                        for n in self._graph.keys()
                        if lowered in (self._tool_functionality(n) or "").lower()
                    ][:5]

        nodes_set, edges_set = self._bfs_subgraph(seed_nodes, max_depth=max_depth, max_nodes=max_nodes)
        nodes = [self.normalize_node(n) for n in nodes_set]
        edges = [self.normalize_edge(e) for e in edges_set]

        return {"nodes": nodes, "edges": edges, "total_nodes": len(nodes), "total_edges": len(edges)}

    async def add_entity(self, triples: list[dict], **kwargs) -> bool:
        return False

    async def get_sample_nodes(self, num: int = 50, **kwargs) -> dict[str, list]:
        return await self.query_nodes("*", max_nodes=num, **kwargs)

    def normalize_node(self, raw_node: Any) -> dict[str, Any]:
        node_name = str(raw_node)
        properties: dict[str, Any] = {}
        labels: list[str] = []

        category = self._tool_category(node_name)
        if category:
            labels.append(category)
            properties["category"] = category

        functionality = self._tool_functionality(node_name)
        if functionality:
            properties["functionality"] = functionality

        inputs = self._tool_inputs(node_name)
        outputs = self._tool_outputs(node_name)
        if inputs:
            properties["inputs"] = inputs
        if outputs:
            properties["outputs"] = outputs

        entity_type = category or self._infer_node_type(node_name)

        return self._create_standard_node(
            node_id=node_name,
            name=node_name,
            entity_type=entity_type,
            labels=labels,
            properties=properties,
            source="scitoolkg",
        )

    def normalize_edge(self, raw_edge: Any) -> dict[str, Any]:
        source, rel, target = raw_edge
        edge_id = f"{source}::{rel}::{target}"
        return self._create_standard_edge(
            edge_id=edge_id,
            source_id=source,
            target_id=target,
            edge_type=rel,
            properties={},
        )

    async def get_labels(self) -> list[str]:
        categories = {t.category for t in get_scitoolkg_tools() if t.category}
        base = {"Tool", "Input", "Output", "Category"}
        return sorted(base | set(categories))

    def _bfs_subgraph(
        self, seed_nodes: list[str], max_depth: int, max_nodes: int
    ) -> tuple[set[str], set[tuple[str, str, str]]]:
        visited: set[str] = set()
        edges: set[tuple[str, str, str]] = set()

        q: deque[tuple[str, int]] = deque()
        for s in seed_nodes:
            if s in self._graph:
                q.append((s, 0))
                visited.add(s)

        while q and len(visited) < max_nodes:
            node, depth = q.popleft()
            if depth >= max_depth:
                continue

            for rel, target in self._graph.get(node, []):
                edges.add((node, rel, target))
                if target not in visited and len(visited) < max_nodes:
                    visited.add(target)
                    q.append((target, depth + 1))

            for rel, source in self._reverse.get(node, []):
                edges.add((source, rel, node))
                if source not in visited and len(visited) < max_nodes:
                    visited.add(source)
                    q.append((source, depth + 1))

        return visited, edges

    def _tool_category(self, node: str) -> str | None:
        for rel, target in self._graph.get(node, []):
            if rel == "is a" and target.endswith(" Tool"):
                return target
        return None

    def _tool_functionality(self, node: str) -> str | None:
        for rel, target in self._graph.get(node, []):
            if rel == "has the functionality that":
                return target
        return None

    def _tool_inputs(self, node: str) -> list[str]:
        return [t for rel, t in self._graph.get(node, []) if rel == "inputs"]

    def _tool_outputs(self, node: str) -> list[str]:
        return [t for rel, t in self._graph.get(node, []) if rel == "outputs"]

    def _infer_node_type(self, node: str) -> str:
        if any(rel == "is the input of" for rel, _ in self._graph.get(node, [])):
            return "Input"
        if any(rel == "is the output of" for rel, _ in self._graph.get(node, [])):
            return "Output"
        if any(rel == "is a" for rel, _ in self._reverse.get(node, [])):
            return "Category"
        return "Entity"

