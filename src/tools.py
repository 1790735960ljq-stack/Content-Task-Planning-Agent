"""四个本地工具。

工具只返回观察结果。超预算、超期或失败时，不会在这里自动改选另一个方案。
"""

from __future__ import annotations

from .state import STEP_LIMIT_TEXT, PlanStore, TaskState, normalize_number

VARIANTS = ("standard", "lean")
FAILURE_TEXT = {
    "search_assets": "素材检索服务暂时失败",
    "generate_script": "脚本生成服务暂时失败",
    "estimate_cost": "成本估算服务暂时失败",
    "save_plan": "方案保存服务暂时失败",
}


def _limit_result(state: TaskState, tool: str, args: dict) -> dict:
    payload = {
        "ok": False,
        "tool": tool,
        "retryable": False,
        "stop": True,
        "error": STEP_LIMIT_TEXT,
        "summary": STEP_LIMIT_TEXT,
    }
    state.add_step(tool, args, "failed", STEP_LIMIT_TEXT, payload)
    return payload


def _injected_failure(state: TaskState, pending: set[str], tool: str, args: dict, extra: dict | None = None) -> dict | None:
    if tool not in pending:
        return None
    pending.remove(tool)
    summary = FAILURE_TEXT[tool]
    payload = {
        "ok": False,
        "tool": tool,
        "retryable": True,
        "stop": False,
        "error": summary,
        "summary": summary,
    }
    if extra:
        payload.update(extra)
    state.add_step(tool, args, "failed", summary, payload)
    return payload


def _reject(state: TaskState, tool: str, args: dict, summary: str, extra: dict | None = None) -> dict:
    payload = {
        "ok": False,
        "tool": tool,
        "retryable": False,
        "stop": False,
        "error": summary,
        "summary": summary,
    }
    if extra:
        payload.update(extra)
    state.add_step(tool, args, "failed", summary, payload)
    return payload


def search_assets(state: TaskState, assets: list[dict], theme: str, pending_failures: set[str] | None = None) -> dict:
    pending_failures = pending_failures if pending_failures is not None else set()
    args = {"theme": theme}
    if not state.begin_call():
        return _limit_result(state, "search_assets", args)
    injected = _injected_failure(state, pending_failures, "search_assets", args)
    if injected:
        return injected

    specific = [item for item in assets if theme in item.get("themes", [])]
    generic = [item for item in assets if "全部" in item.get("themes", [])]
    chosen = specific + [item for item in generic if item not in specific]
    if not chosen:
        chosen = assets[:1]
        summary = "没有匹配素材，返回了备用素材"
    elif not specific:
        summary = "没有该主题的专用素材，返回通用素材：" + "、".join(item["title"] for item in chosen)
    else:
        summary = f"检索到 {len(specific)} 条主题素材：" + "、".join(item["title"] for item in specific)
    state.assets = chosen
    payload = {"ok": True, "tool": "search_assets", "assets": chosen, "summary": summary}
    state.add_step("search_assets", args, "succeeded", summary, payload)
    return payload


def render_script(theme: str, variant: str, assets: list[dict]) -> str:
    names = "、".join(item["title"] for item in assets)
    if variant == "lean":
        return f"【精简方案】主题《{theme}》。使用素材：{names}。产出 1 条短视频，压缩制作环节以控制费用和耗时。"
    return f"【标准方案】主题《{theme}》。使用素材：{names}。产出预热、正片、复盘 3 条内容。"


def generate_script(
    state: TaskState,
    theme: str,
    variant: str,
    pending_failures: set[str] | None = None,
) -> dict:
    pending_failures = pending_failures if pending_failures is not None else set()
    args = {"theme": theme, "variant": variant}
    if not state.begin_call():
        return _limit_result(state, "generate_script", args)
    if variant not in VARIANTS:
        return _reject(
            state,
            "generate_script",
            args,
            "variant 只能是 standard 或 lean",
            {"variant": variant},
        )
    injected = _injected_failure(
        state,
        pending_failures,
        "generate_script",
        args,
        {"variant": variant},
    )
    if injected:
        return injected
    if not state.assets:
        return _reject(state, "generate_script", args, "尚未检索到素材，无法生成脚本", {"variant": variant})

    script = render_script(theme, variant, state.assets)
    state.scripts[variant] = script
    payload = {
        "ok": True,
        "tool": "generate_script",
        "variant": variant,
        "script": script,
        "summary": script,
    }
    state.add_step("generate_script", args, "succeeded", script, payload)
    return payload


def estimate_cost(state: TaskState, cost_table: dict, variant: str, pending_failures: set[str] | None = None) -> dict:
    pending_failures = pending_failures if pending_failures is not None else set()
    args = {"variant": variant}
    if not state.begin_call():
        return _limit_result(state, "estimate_cost", args)
    row = cost_table["variants"].get(variant)
    if row is None:
        return _reject(state, "estimate_cost", args, "variant 只能是 standard 或 lean", {"variant": variant})
    injected = _injected_failure(state, pending_failures, "estimate_cost", args, {"variant": variant})
    if injected:
        return injected
    if variant not in state.scripts:
        return _reject(
            state,
            "estimate_cost",
            args,
            "请先生成该方案的脚本。失败的生成记录不能当作脚本已完成。",
            {"variant": variant},
        )

    line_items = row["line_items"]
    line_sum = sum(item["amount"] for item in line_items)
    if line_sum != row["cost"]:
        return _reject(state, "estimate_cost", args, "费用表明细与合计不一致", {"variant": variant})

    cost = normalize_number(row["cost"])
    duration_days = row["duration_days"]
    days_left = (state.deadline - state.today).days
    budget = normalize_number(state.budget)
    over_budget = cost > budget
    over_deadline = duration_days > days_left
    budget_gap = normalize_number(cost - budget) if over_budget else 0
    days_gap = duration_days - days_left if over_deadline else 0
    unit = cost_table.get("unit", "模拟单位")
    summary = (
        f"{row['label']}估算 {cost} {unit}，耗时 {duration_days} 天，距离截止还有 {days_left} 天。"
    )
    if over_budget:
        summary += f"超出预算 {budget_gap}。"
    else:
        summary += "未超出预算。"
    if over_deadline:
        summary += f"赶不上截止时间，还差 {days_gap} 天。"
    else:
        summary += "可以在截止时间内完成。"
    summary += "本工具不会自动更换方案。"

    stored = {
        "variant": variant,
        "label": row["label"],
        "cost": cost,
        "duration_days": duration_days,
        "line_items": line_items,
        "days_left": days_left,
        "over_budget": over_budget,
        "over_deadline": over_deadline,
        "budget_gap": budget_gap,
        "days_gap": days_gap,
        "unit": unit,
    }
    state.estimates[variant] = stored
    payload = {"ok": True, "tool": "estimate_cost", "summary": summary, **stored}
    # 估算成功只说明数字已经算出。超预算仍是成功的观察，不是工具失败。
    state.add_step("estimate_cost", args, "succeeded", summary, payload)
    return payload


def save_plan(state: TaskState, store: PlanStore, cost_table: dict, variant: str, pending_failures: set[str] | None = None) -> dict:
    pending_failures = pending_failures if pending_failures is not None else set()
    args = {"variant": variant}
    if not state.begin_call():
        return _limit_result(state, "save_plan", args)
    row = cost_table["variants"].get(variant)
    if row is None:
        return _reject(state, "save_plan", args, "variant 只能是 standard 或 lean", {"variant": variant})
    injected = _injected_failure(state, pending_failures, "save_plan", args, {"variant": variant})
    if injected:
        return injected

    label = row["label"]
    script = state.scripts.get(variant)
    estimate = state.estimates.get(variant)
    if not script:
        return _reject(
            state,
            "save_plan",
            args,
            f"拒绝保存{label}：还没有该方案的成功脚本。失败记录不能当作已完成。",
            {"variant": variant},
        )
    if not estimate:
        return _reject(
            state,
            "save_plan",
            args,
            f"拒绝保存{label}：还没有该方案的成功估算。",
            {"variant": variant},
        )
    if estimate["over_budget"] or estimate["over_deadline"]:
        reasons = []
        if estimate["over_budget"]:
            reasons.append(f"超出预算 {estimate['budget_gap']}")
        if estimate["over_deadline"]:
            reasons.append(f"赶不上截止时间，差 {estimate['days_gap']} 天")
        summary = f"拒绝保存{label}：{'；'.join(reasons)}。请根据估算结果另选方案或停止。"
        return _reject(state, "save_plan", args, summary, {"variant": variant})

    plan_body = {
        "theme": state.theme,
        "variant": variant,
        "label": label,
        "script": script,
        "cost": estimate["cost"],
        "duration_days": estimate["duration_days"],
        "line_items": estimate["line_items"],
        "budget": normalize_number(state.budget),
        "deadline": state.deadline.isoformat(),
    }
    plan, created = store.save(f"{state.theme}|{variant}", plan_body)
    state.saved_plan = plan
    state.plan_reused = not created
    if created:
        summary = f"已保存{label}，方案编号 {plan['plan_id']}，费用 {plan['cost']}。"
    else:
        summary = f"该方案已存在，返回原编号 {plan['plan_id']}，费用仍为 {plan['cost']}，不重复计费。"
    payload = {
        "ok": True,
        "tool": "save_plan",
        "created": created,
        "plan": plan,
        "total_cost": plan["cost"],
        "summary": summary,
    }
    state.add_step("save_plan", args, "succeeded", summary, payload)
    return payload
