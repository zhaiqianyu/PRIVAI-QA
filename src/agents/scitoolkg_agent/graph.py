from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Sequence

import json_repair
from langchain.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph, add_messages

from src.agents.common import BaseAgent, load_chat_model
from src.knowledge.scitoolkg import recommend_tool_path
from src.utils import logger

from .context import Context


def _repo_root() -> Path:
    # src/agents/scitoolkg_agent/graph.py -> repo root
    return Path(__file__).resolve().parents[3]


def _ensure_toolsagent_sys_path() -> None:
    toolsagent_dir = _repo_root() / "SciToolAgent" / "ToolsAgent"
    toolsagent_path = str(toolsagent_dir)
    if toolsagent_path not in sys.path:
        sys.path.insert(0, toolsagent_path)


def _last_user_text(messages: Sequence[AnyMessage]) -> str:
    for msg in reversed(list(messages)):
        if isinstance(msg, HumanMessage):
            return msg.content or ""
    return ""


def _format_tool_candidates(tools: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for tool_info in tools:
        lines.append(f"- name: {tool_info.get('name')}")
        if tool_info.get("functionality"):
            lines.append(f"  functionality: {tool_info.get('functionality')}")
        if tool_info.get("inputs"):
            lines.append(f"  inputs: {', '.join(tool_info.get('inputs') or [])}")
        if tool_info.get("outputs"):
            lines.append(f"  outputs: {', '.join(tool_info.get('outputs') or [])}")
    return "\n".join(lines)


def _parse_plan_json(text: str) -> list[dict[str, Any]]:
    try:
        obj = json_repair.loads(text)
    except Exception:
        return []
    if not isinstance(obj, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in obj:
        if isinstance(item, dict):
            normalized.append(item)
        elif isinstance(item, str):
            normalized.append({"tool_name": item})
    return normalized


def _normalize_tool_name(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _find_tool_info(candidate_tools: list[dict[str, Any]], tool_name: str) -> dict[str, Any] | None:
    lowered = tool_name.lower()
    for tool_info in candidate_tools:
        if str(tool_info.get("name", "")).lower() == lowered:
            return tool_info
    return None


@dataclass
class SciToolKGState:
    messages: Annotated[Sequence[AnyMessage], add_messages] = field(default_factory=list)
    plan: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, str]] = field(default_factory=list)
    candidate_tools: list[dict[str, Any]] = field(default_factory=list)


class SciToolKGAgent(BaseAgent):
    name = "SciToolKG 多工具智能体"
    description = "基于 SciToolKG 的规划-执行-总结（Planner/Executor/Summarizer）流程"
    context_schema = Context

    async def get_graph(self, **kwargs):
        if self.graph:
            return self.graph

        workflow: StateGraph[SciToolKGState] = StateGraph(SciToolKGState)

        async def planner(state: SciToolKGState) -> dict[str, Any]:
            ctx = self.context_schema.from_file(module_name=self.module_name)
            question = _last_user_text(state.messages).strip()
            if not question:
                return {"messages": [AIMessage(content="请先给出一个需要解决的科学问题。")]}

            rec = recommend_tool_path(question, top_k=ctx.candidate_tools_top_k)
            candidate_tools = rec.get("tools", []) if isinstance(rec, dict) else []
            candidate_tools_text = _format_tool_candidates(candidate_tools)

            model = load_chat_model(ctx.model)
            prompt = (
                "你是一个科学工具链规划器（Planner）。\n"
                "基于用户问题，从候选工具中选择一个尽量短的工具序列（按顺序），用于获得解决问题所需的信息。\n"
                f"最多选择 {ctx.max_steps} 个工具。\n\n"
                f"用户问题：\n{question}\n\n"
                "候选工具：\n"
                f"{candidate_tools_text}\n\n"
                "输出要求：只输出 JSON 数组。数组元素为对象，包含字段：tool_name, goal。\n"
                "示例：[{\"tool_name\":\"ToolA\",\"goal\":\"...\"},{\"tool_name\":\"ToolB\",\"goal\":\"...\"}]\n"
            )
            resp = await model.ainvoke([SystemMessage(content=ctx.system_prompt), HumanMessage(content=prompt)])
            plan = _parse_plan_json(getattr(resp, "content", "") or "")

            filtered: list[dict[str, Any]] = []
            for item in plan:
                tool_name = _normalize_tool_name(item.get("tool_name") or item.get("name"))
                if not tool_name:
                    continue
                if _find_tool_info(candidate_tools, tool_name) is None:
                    continue
                filtered.append({"tool_name": tool_name, "goal": str(item.get("goal", "")).strip()})
                if len(filtered) >= ctx.max_steps:
                    break

            if not filtered:
                return {
                    "candidate_tools": candidate_tools,
                    "messages": [AIMessage(content="未能从 SciToolKG 候选工具中生成可执行的工具序列。")],
                }

            plan_text = "\n".join(
                f"{i + 1}. {p['tool_name']}{(' - ' + p['goal']) if p.get('goal') else ''}"
                for i, p in enumerate(filtered)
            )
            return {
                "candidate_tools": candidate_tools,
                "plan": filtered,
                "messages": [AIMessage(content=f"规划完成，将依次执行以下工具：\n{plan_text}")],
            }

        async def executor(state: SciToolKGState) -> dict[str, Any]:
            ctx = self.context_schema.from_file(module_name=self.module_name)
            question = _last_user_text(state.messages).strip()
            model = load_chat_model(ctx.model)

            _ensure_toolsagent_sys_path()
            from tool_runner import run_task_in_process  # type: ignore

            tool_results: list[dict[str, str]] = list(state.tool_results)
            for step in state.plan:
                tool_name = _normalize_tool_name(step.get("tool_name"))
                tool_info = _find_tool_info(state.candidate_tools, tool_name) or {}

                previous_outputs = "\n\n".join(
                    f"[{r['tool_name']} output]\n{r['output']}" for r in tool_results[-3:]
                )
                input_prompt = (
                    "你是一个科学工具执行器（Executor）的输入生成模块。\n"
                    "为即将调用的工具生成“可直接传入工具函数的输入字符串”，不要输出解释。\n\n"
                    f"用户问题：\n{question}\n\n"
                    f"工具名称：{tool_name}\n"
                    f"工具功能：{tool_info.get('functionality') or ''}\n"
                    f"工具输入：{', '.join(tool_info.get('inputs') or [])}\n"
                    f"工具输出：{', '.join(tool_info.get('outputs') or [])}\n\n"
                    f"已获得的中间结果：\n{previous_outputs}\n\n"
                    "只输出输入字符串："
                )
                input_resp = await model.ainvoke(
                    [SystemMessage(content=ctx.system_prompt), HumanMessage(content=input_prompt)]
                )
                tool_input = (getattr(input_resp, "content", "") or "").strip()

                try:
                    output = await run_task_in_process(tool_name, tool_input)
                except Exception as e:
                    output = f"[tool_error] {type(e).__name__}: {e}"

                tool_results.append({"tool_name": tool_name, "input": tool_input, "output": str(output)})

            return {"tool_results": tool_results}

        async def summarizer(state: SciToolKGState) -> dict[str, Any]:
            ctx = self.context_schema.from_file(module_name=self.module_name)
            question = _last_user_text(state.messages).strip()
            model = load_chat_model(ctx.model)

            plan_text = "\n".join(f"{i + 1}. {p.get('tool_name')}" for i, p in enumerate(state.plan))
            results_text = "\n\n".join(
                f"## {r['tool_name']}\n### input\n{r['input']}\n### output\n{r['output']}" for r in state.tool_results
            )

            prompt = (
                "你是一个科学问题总结器（Summarizer）。\n"
                "基于工具执行的结果，给出对用户问题的最终回答。\n"
                "如果某一步输出包含错误（以 [tool_error] 开头），请说明该步未成功，并尽量在现有信息下完成回答。\n\n"
                f"用户问题：\n{question}\n\n"
                f"执行计划：\n{plan_text}\n\n"
                f"工具结果：\n{results_text}\n\n"
                "请输出最终回答："
            )
            resp = await model.ainvoke([SystemMessage(content=ctx.system_prompt), HumanMessage(content=prompt)])
            answer = getattr(resp, "content", "") or ""
            return {"messages": [AIMessage(content=answer)]}

        workflow.add_node("planner", planner)
        workflow.add_node("executor", executor)
        workflow.add_node("summarizer", summarizer)

        workflow.add_edge(START, "planner")
        workflow.add_edge("planner", "executor")
        workflow.add_edge("executor", "summarizer")
        workflow.add_edge("summarizer", END)

        self.graph = workflow.compile(checkpointer=await self._get_checkpointer())
        logger.info("SciToolKGAgent graph compiled")
        return self.graph
