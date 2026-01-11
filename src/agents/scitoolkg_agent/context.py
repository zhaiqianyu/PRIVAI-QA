from __future__ import annotations

from dataclasses import dataclass, field

from src import config as sys_config
from src.agents.common.context import BaseContext


@dataclass(kw_only=True)
class Context(BaseContext):
    model: str = field(
        default=sys_config.default_model,
        metadata={"name": "模型", "options": [], "description": "用于规划/执行输入生成/总结的模型"},
    )

    candidate_tools_top_k: int = field(
        default=8,
        metadata={"name": "候选工具数", "description": "Planner 看到的候选工具数量（来自 SciToolKG 推荐）"},
    )

    max_steps: int = field(
        default=3,
        metadata={"name": "最大步骤数", "description": "最多执行多少个工具（过大可能会很慢）"},
    )
