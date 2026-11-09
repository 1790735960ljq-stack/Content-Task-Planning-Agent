"""ReAct 循环。

LangChain create_agent 负责模型选工具、执行、把观察交回模型。
这里不写“超预算就改选 lean”。是否更换方案由模型看到工具结果后决定。
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphRecursionError

from .model import FlowMockModel, ModelConfigError, _safe_text, build_chat_model, live_model_name
from .resources import DEFAULT_PLAN_STORE, ROOT, load_assets, load_cost_table
from .state import PlanStore, TaskState, build_result, empty_result, normalize_number
from .tools import estimate_cost, generate_script, save_plan, search_assets
from .trace import build_trace, final_text

PROMPT_ID = "skeleton-v1"

SYSTEM_PROMPT = """你是运营人员的内容活动规划助手。目标是在预算和截止时间内完成一份可保存的内容活动方案。

每次只调用一个工具。调用工具时，在回复文字里用一句话写出依据。

工具：
- search_assets(theme)：检索素材
- generate_script(theme, variant)：生成脚本。variant 只能是 standard 或 lean
- estimate_cost(variant)：估算该方案的费用和耗时，并说明是否超预算、是否赶不上截止时间
- save_plan(variant)：保存估算通过的方案

执行骨架：
1. 先检索素材。
2. 先生成并估算 standard。
3. 如果 standard 超预算或超期，不要保存它，再生成并估算 lean。
4. 只有估算结果表明未超预算且能在截止时间内完成时，才能保存。
5. 如果两种方案都不可行，停止，并说明预算缺口或时间缺口。不要保存。

约束：
- 估算和保存都不会替你更换方案。看到超限或拒绝后，由你决定下一步。
- 工具返回 ok 为 false 表示这一步失败。失败结果不能当作成功。此前已经成功的素材、脚本和估算仍然有效。
- 不要把两次估算的费用相加。最终费用以成功保存的方案为准。
"""


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def build_tools(state: TaskState, assets: list[dict], cost_table: dict, pending_failures: set[str], store: PlanStore):
    @tool("search_assets")
    def search_assets_tool(theme: str) -> str:
        """按主题检索可用的合成素材。"""
        with state.lock:
            payload = search_assets(state, assets, theme, pending_failures)
        return _dump(payload)

    @tool("generate_script")
    def generate_script_tool(theme: str, variant: str) -> str:
        """生成活动脚本。variant 只能是 standard（标准方案）或 lean（精简方案）。"""
        with state.lock:
            payload = generate_script(state, theme, variant, pending_failures)
        return _dump(payload)

    @tool("estimate_cost")
    def estimate_cost_tool(variant: str) -> str:
        """估算指定方案的费用和耗时，并标明是否超预算或超期。不会自动更换方案。"""
        with state.lock:
            payload = estimate_cost(state, cost_table, variant, pending_failures)
        return _dump(payload)

    @tool("save_plan")
    def save_plan_tool(variant: str) -> str:
        """保存方案。缺少成功脚本、缺少成功估算、超预算或超期时会拒绝。重复保存同一方案不会重复计费。"""
        with state.lock:
            payload = save_plan(state, store, cost_table, variant, pending_failures)
        return _dump(payload)

    return [search_assets_tool, generate_script_tool, estimate_cost_tool, save_plan_tool]


def user_message(theme: str, budget, deadline: date) -> str:
    return (
        "请为运营活动做内容方案。\n"
        f"主题：{theme}\n"
        f"预算：{normalize_number(budget)} 个模拟单位\n"
        f"截止时间：{deadline.isoformat()}\n"
        "请使用工具完成规划。不要在没有工具结果时声称已经保存。"
    )


def _parse_deadline(value: str | None):
    if value is None or str(value).strip() == "":
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return "invalid"


def run_task(
    theme: str | None,
    budget: float | None,
    deadline: str | None,
    *,
    mode: str | None = None,
    fail_tool: str | None = None,
    max_steps: int = 8,
    plan_store_path: Path | None = None,
    today: date | None = None,
) -> dict:
    load_dotenv(ROOT / ".env", override=False)
    task_input = {
        "theme": theme,
        "budget": None if budget is None else normalize_number(budget),
        "deadline": deadline,
        "fail_tool": fail_tool,
        "max_steps": max_steps,
        "mode": mode or os.getenv("AGENT_MODE", "live"),
    }
    if not theme or not str(theme).strip():
        return empty_result("missing_theme", task_input)
    if budget is None:
        return empty_result("missing_budget", task_input)
    parsed_deadline = _parse_deadline(deadline)
    if parsed_deadline is None:
        return empty_result("missing_deadline", task_input)
    if parsed_deadline == "invalid":
        return empty_result("invalid_deadline", task_input)

    resolved_mode = task_input["mode"]
    if resolved_mode not in {"live", "mock"}:
        return empty_result("model_unavailable", task_input, "AGENT_MODE 只能是 live 或 mock")

    assets = load_assets()
    cost_table = load_cost_table()
    state = TaskState(
        theme=str(theme).strip(),
        budget=float(budget),
        deadline=parsed_deadline,
        today=today or date.today(),
        variant_names=list(cost_table["variant_order"]),
        max_steps=max_steps,
        model_mode=resolved_mode,
        proves_model_decision=resolved_mode == "live",
        llm_model=live_model_name() if resolved_mode == "live" else "flow-mock",
    )
    store = PlanStore(plan_store_path or DEFAULT_PLAN_STORE)
    pending_failures = {fail_tool} if fail_tool else set()
    tools = build_tools(state, assets, cost_table, pending_failures, store)
    try:
        chat_model = build_chat_model() if resolved_mode == "live" else FlowMockModel()
    except ModelConfigError as exc:
        return empty_result("model_unavailable", task_input, str(exc))

    agent = create_agent(
        model=chat_model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=MemorySaver(),
    )
    config = {
        "recursion_limit": max(max_steps * 2 + 4, 8),
        "configurable": {"thread_id": "content-task"},
    }
    messages = []
    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": user_message(state.theme, state.budget, state.deadline)}]},
            config,
        )
        messages = result.get("messages", [])
    except GraphRecursionError:
        state.hit_step_limit = True
        snapshot = agent.get_state(config)
        messages = snapshot.values.get("messages", [])
    except Exception as exc:
        trace = build_trace(messages)
        output = build_result(state, trace, "", task_input)
        if output["status"] != "completed":
            output["status"] = "incomplete"
            output["reason"] = "model_call_failed"
            output["reason_text"] = f"模型调用失败：{_safe_text(str(exc))}"
            output["final_message"] = output["reason_text"]
        return output

    trace = build_trace(messages)
    output = build_result(state, trace, final_text(messages), task_input)
    output["prompt_id"] = PROMPT_ID
    return output
