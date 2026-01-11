import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


@dataclass(frozen=True)
class SciToolKGTool:
    name: str
    category: str | None
    functionality: str | None
    inputs: list[str]
    outputs: list[str]
    source: str | None
    needs_security_check: bool | None


def _repo_root() -> Path:
    # src/knowledge/scitoolkg.py -> repo root
    return Path(__file__).resolve().parents[2]


def get_scitoolkg_persist_dir() -> Path:
    return _repo_root() / "SciToolAgent" / "KG" / "storage_graph_large"


def is_scitoolkg_available() -> bool:
    persist_dir = get_scitoolkg_persist_dir()
    return (persist_dir / "graph_store.json").exists()


@lru_cache(maxsize=1)
def load_scitoolkg_graph() -> dict[str, list[tuple[str, str]]]:
    """
    Load SciToolKG from SciToolAgent persisted `graph_store.json`.

    Format:
    {
      "graph_dict": {
        "ToolA": [["is a", "Biological Tool"], ["inputs", "protein sequence"], ...],
        ...
      }
    }
    """
    graph_store_path = get_scitoolkg_persist_dir() / "graph_store.json"
    raw = json.loads(graph_store_path.read_text(encoding="utf-8"))
    graph_dict = raw.get("graph_dict", {})
    graph: dict[str, list[tuple[str, str]]] = {}
    for node, edges in graph_dict.items():
        normalized_edges: list[tuple[str, str]] = []
        for rel, target in edges:
            if not isinstance(rel, str) or not isinstance(target, str):
                continue
            normalized_edges.append((rel, target))
        graph[str(node)] = normalized_edges
    return graph


@lru_cache(maxsize=1)
def build_reverse_graph() -> dict[str, list[tuple[str, str]]]:
    reverse: dict[str, list[tuple[str, str]]] = {}
    for source, edges in load_scitoolkg_graph().items():
        for rel, target in edges:
            reverse.setdefault(target, []).append((rel, source))
    return reverse


def _is_tool_node(node: str, graph: dict[str, list[tuple[str, str]]]) -> bool:
    return any(rel == "is a" and target.endswith(" Tool") for rel, target in graph.get(node, []))


def _tool_property(tool: str, rel_name: str) -> list[str]:
    return [target for rel, target in load_scitoolkg_graph().get(tool, []) if rel == rel_name]


@lru_cache(maxsize=1)
def get_scitoolkg_tools() -> list[SciToolKGTool]:
    graph = load_scitoolkg_graph()
    tools: list[SciToolKGTool] = []
    for node in graph.keys():
        if not _is_tool_node(node, graph):
            continue

        categories = _tool_property(node, "is a")
        functionality = next(iter(_tool_property(node, "has the functionality that")), None)
        source = next(iter(_tool_property(node, "is sourced from")), None)
        needs_security = None
        if _tool_property(node, "needs") and "Security Check" in _tool_property(node, "needs"):
            needs_security = True
        if _tool_property(node, "does not need") and "Security Check" in _tool_property(node, "does not need"):
            needs_security = False

        tools.append(
            SciToolKGTool(
                name=node,
                category=categories[0] if categories else None,
                functionality=functionality,
                inputs=_tool_property(node, "inputs"),
                outputs=_tool_property(node, "outputs"),
                source=source,
                needs_security_check=needs_security,
            )
        )
    return tools


def get_scitoolkg_stats() -> dict[str, Any]:
    graph = load_scitoolkg_graph()
    total_nodes = len(graph)
    total_edges = sum(len(edges) for edges in graph.values())

    tool_type_counts: dict[str, int] = {}
    for tool in get_scitoolkg_tools():
        category = tool.category or "unknown"
        tool_type_counts[category] = tool_type_counts.get(category, 0) + 1

    return {
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "entity_types": [{"type": k, "count": v} for k, v in sorted(tool_type_counts.items(), key=lambda x: x[1], reverse=True)],
    }


def _tokenize(text: str) -> set[str]:
    tokens = {t.lower() for t in _WORD_RE.findall(text)}
    stop = {"the", "a", "an", "to", "of", "and", "or", "in", "on", "for", "with", "is", "are", "what", "how", "please"}
    return {t for t in tokens if t not in stop and len(t) >= 2}


def recommend_tool_path(question: str, top_k: int = 5) -> dict[str, Any]:
    """
    Recommend a tool path based on SciToolKG only (no tool execution).
    """
    tokens = _tokenize(question)
    tools = get_scitoolkg_tools()

    scored: list[tuple[int, SciToolKGTool]] = []
    for tool in tools:
        haystack = " ".join(
            [
                tool.name,
                tool.category or "",
                tool.functionality or "",
                " ".join(tool.inputs),
                " ".join(tool.outputs),
            ]
        ).lower()
        score = sum(1 for t in tokens if t in haystack)
        if score > 0:
            scored.append((score, tool))

    scored.sort(key=lambda x: (x[0], x[1].name), reverse=True)
    selected = [tool for _, tool in scored[: max(1, top_k)]]

    tool_path = [t.name for t in selected]
    tool_infos = [
        {
            "name": t.name,
            "category": t.category,
            "functionality": t.functionality,
            "inputs": t.inputs,
            "outputs": t.outputs,
            "source": t.source,
            "needs_security_check": t.needs_security_check,
        }
        for t in selected
    ]

    final_answer = "推荐工具路径（未执行工具）：" + (" -> ".join(tool_path) if tool_path else "未找到匹配工具")
    return {"tool_path": tool_path, "tools": tool_infos, "final_answer": final_answer}

