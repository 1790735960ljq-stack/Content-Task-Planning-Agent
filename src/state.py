"""任务状态和方案库。

步骤日志只追加，不覆盖。费用合计只取已保存方案，不把试算金额相加。
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from threading import Lock

TOOL_LABELS = {
    "search_assets": "检索素材",
    "generate_script": "生成脚本",
    "estimate_cost": "估算成本",
    "save_plan": "保存方案",
}

STEP_LIMIT_TEXT = "已达到工具调用次数上限，任务未完成"

REASON_TEXT = {
    "missing_theme": "缺少主题，未开始规划",
    "missing_budget": "缺少预算，未开始规划",
    "missing_deadline": "缺少截止时间，未开始规划",
    "invalid_deadline": "截止时间格式无效，应为 YYYY-MM-DD",
    "model_unavailable": "真实模型不可用。请配置 LLM_API_KEY，或显式使用 mock 只做流程验证",
    "model_call_failed": "模型调用失败，任务未完成",
    "no_feasible_plan": "没有同时满足预算和截止时间的方案",
    "max_steps": "达到工具调用次数上限，任务未完成",
    "tool_failure": "存在失败步骤，且尚未保存可行方案",
    "unfinished": "模型已停止，但尚未保存可行方案",
    "completed": "已在预算和截止时间内保存方案",
}


def normalize_number(value):
    """整数形式的浮点数写成整数，方便阅读费用。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


@dataclass
class TaskState:
    theme: str
    budget: float
    deadline: date
    today: date
    variant_names: list[str]
    max_steps: int = 8
    steps: list[dict] = field(default_factory=list)
    assets: list[dict] | None = None
    scripts: dict = field(default_factory=dict)
    estimates: dict = field(default_factory=dict)
    saved_plan: dict | None = None
    plan_reused: bool = False
    tool_calls: int = 0
    hit_step_limit: bool = False
    model_mode: str | None = None
    llm_model: str | None = None
    proves_model_decision: bool = False
    lock: Lock = field(default_factory=Lock, repr=False)

    def begin_call(self) -> bool:
        """占用一次工具调用。达到上限时返回 False，且不把次数再加一。"""
        if self.tool_calls >= self.max_steps:
            self.hit_step_limit = True
            return False
        self.tool_calls += 1
        return True

    def add_step(self, tool: str, args: dict, status: str, summary: str, result: dict | None = None) -> dict:
        record = {
            "index": len(self.steps) + 1,
            "tool": tool,
            "label": TOOL_LABELS.get(tool, tool),
            "args": deepcopy(args),
            "status": status,
            "result_summary": summary,
        }
        if result is not None:
            record["result"] = deepcopy(result)
        self.steps.append(record)
        return record


class PlanStore:
    """按主题和方案类型保存方案。同一键再次保存时返回原记录。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self._data = {"plans": []}

    def save(self, key: str, plan_body: dict) -> tuple[dict, bool]:
        for item in self._data["plans"]:
            if item["key"] == key:
                return item["plan"], False
        plan = {"plan_id": f"plan-{len(self._data['plans']) + 1:04d}", **plan_body}
        self._data["plans"].append({"key": key, "plan": plan})
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return plan, True


def build_stages(steps: list[dict]) -> list[dict]:
    stages = []
    for tool, label in TOOL_LABELS.items():
        attempts = [step for step in steps if step["tool"] == tool]
        if not attempts:
            status = "pending"
        elif any(step["status"] == "failed" for step in attempts) and any(
            step["status"] == "succeeded" for step in attempts
        ):
            # 阶段上标明“失败后成功”，具体失败记录仍留在 attempts 里。
            status = "succeeded_after_failure"
        else:
            status = attempts[-1]["status"]
        stages.append(
            {
                "tool": tool,
                "label": label,
                "status": status,
                "attempts": deepcopy(attempts),
            }
        )
    return stages


def build_gap(state: TaskState) -> dict | None:
    if not state.estimates:
        return None
    estimates = []
    budget_gaps = []
    day_gaps = []
    for name in state.variant_names:
        estimate = state.estimates.get(name)
        if not estimate:
            continue
        item = {
            "variant": name,
            "label": estimate["label"],
            "cost": estimate["cost"],
            "budget_gap": estimate["budget_gap"],
            "duration_days": estimate["duration_days"],
            "days_left": estimate["days_left"],
            "days_gap": estimate["days_gap"],
            "over_budget": estimate["over_budget"],
            "over_deadline": estimate["over_deadline"],
        }
        estimates.append(item)
        if estimate["over_budget"]:
            budget_gaps.append(estimate["budget_gap"])
        if estimate["over_deadline"]:
            day_gaps.append(estimate["days_gap"])
    feasible = [
        item["variant"]
        for item in estimates
        if not item["over_budget"] and not item["over_deadline"]
    ]
    none_fit = not feasible
    return {
        "budget": normalize_number(state.budget),
        "deadline": state.deadline.isoformat(),
        "today": state.today.isoformat(),
        "estimates": estimates,
        "feasible_variants": feasible,
        "budget_shortfall": min(budget_gaps) if budget_gaps and none_fit else 0,
        "deadline_shortfall_days": min(day_gaps) if day_gaps and none_fit else 0,
    }


def resolve_status(state: TaskState) -> tuple[str, str]:
    if state.saved_plan:
        return "completed", "completed"
    if state.hit_step_limit:
        return "incomplete", "max_steps"
    if state.variant_names and set(state.estimates) >= set(state.variant_names):
        feasible = [
            name
            for name, estimate in state.estimates.items()
            if not estimate["over_budget"] and not estimate["over_deadline"]
        ]
        if not feasible:
            return "incomplete", "no_feasible_plan"
    if any(step["status"] == "failed" for step in state.steps):
        return "incomplete", "tool_failure"
    return "incomplete", "unfinished"


def build_result(state: TaskState, trace: list[dict], final_message: str, task_input: dict) -> dict:
    status, reason = resolve_status(state)
    total_cost = state.saved_plan["cost"] if state.saved_plan else 0
    return {
        "status": status,
        "reason": None if status == "completed" else reason,
        "reason_text": REASON_TEXT[reason],
        "gap": None if status == "completed" else build_gap(state),
        "model_mode": state.model_mode,
        "proves_model_decision": state.proves_model_decision,
        "llm_model": state.llm_model,
        "prompt_id": "skeleton-v1" if state.model_mode else None,
        "input": task_input,
        "task_plan": build_stages(state.steps),
        "step_log": deepcopy(state.steps),
        "tool_results": {
            "assets": deepcopy(state.assets),
            "scripts": deepcopy(state.scripts),
            "estimates": deepcopy(state.estimates),
        },
        "total_cost": total_cost,
        "saved_plan": deepcopy(state.saved_plan),
        "plan_reused": state.plan_reused if state.saved_plan else False,
        "final_message": final_message,
        "trace": trace,
        "tool_calls": state.tool_calls,
        "max_tool_calls": state.max_steps,
        "today": state.today.isoformat(),
    }


def empty_result(reason: str, task_input: dict, reason_text: str | None = None) -> dict:
    text = reason_text or REASON_TEXT[reason]
    return {
        "status": "incomplete",
        "reason": reason,
        "reason_text": text,
        "gap": None,
        "model_mode": None,
        "proves_model_decision": False,
        "llm_model": None,
        "prompt_id": None,
        "input": task_input,
        "task_plan": build_stages([]),
        "step_log": [],
        "tool_results": {"assets": None, "scripts": {}, "estimates": {}},
        "total_cost": 0,
        "saved_plan": None,
        "plan_reused": False,
        "final_message": text,
        "trace": [],
        "tool_calls": 0,
        "max_tool_calls": task_input.get("max_steps", 8),
        "today": None,
    }
