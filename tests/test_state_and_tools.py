"""工具、费用和状态的单元测试。不调用真实模型。"""

from datetime import date

from src.resources import load_assets, load_cost_table
from src.state import PlanStore, TaskState, build_result
from src.tools import estimate_cost, generate_script, save_plan, search_assets


def make_state(budget=10, deadline="2026-12-31", today="2026-11-09", max_steps=8, theme="开学季"):
    table = load_cost_table()
    return TaskState(
        theme=theme,
        budget=budget,
        deadline=date.fromisoformat(deadline),
        today=date.fromisoformat(today),
        variant_names=list(table["variant_order"]),
        max_steps=max_steps,
    )


def test_cost_table_matches_reference_numbers():
    table = load_cost_table()
    assert table["variants"]["standard"]["cost"] == 12
    assert table["variants"]["lean"]["cost"] == 8
    for name in table["variant_order"]:
        row = table["variants"][name]
        assert sum(item["amount"] for item in row["line_items"]) == row["cost"]


def _store(tmp_path):
    return PlanStore(tmp_path / "plans.json")


def test_over_budget_estimate_is_observation_not_saved(tmp_path):
    state = make_state(budget=10)
    table = load_cost_table()
    assets = load_assets()
    search_assets(state, assets, "开学季")
    generate_script(state, "开学季", "standard")
    estimated = estimate_cost(state, table, "standard")
    assert estimated["ok"] is True
    assert estimated["cost"] == 12
    assert estimated["over_budget"] is True
    assert estimated["budget_gap"] == 2
    assert state.saved_plan is None
    saved = save_plan(state, _store(tmp_path), table, "standard")
    assert saved["ok"] is False
    assert state.steps[-1]["status"] == "failed"
    result = build_result(state, [], "", {"theme": "开学季", "budget": 10})
    assert result["total_cost"] == 0


def test_switch_path_total_is_saved_plan_only(tmp_path):
    state = make_state(budget=10)
    table = load_cost_table()
    assets = load_assets()
    store = _store(tmp_path)
    search_assets(state, assets, "开学季")
    generate_script(state, "开学季", "standard")
    estimate_cost(state, table, "standard")
    refused = save_plan(state, store, table, "standard")
    assert refused["ok"] is False
    generate_script(state, "开学季", "lean")
    lean = estimate_cost(state, table, "lean")
    assert lean["cost"] == 8
    assert lean["over_budget"] is False
    saved = save_plan(state, store, table, "lean")
    assert saved["ok"] is True
    result = build_result(state, [], "", {"theme": "开学季"})
    assert result["total_cost"] == 8
    assert result["total_cost"] != 20
    assert result["saved_plan"]["variant"] == "lean"
    assert state.steps[3]["status"] == "failed"


def test_failed_script_is_not_rewritten(tmp_path):
    state = make_state(budget=15)
    assets = load_assets()
    pending = {"generate_script"}
    search_assets(state, assets, "开学季", pending)
    failed = generate_script(state, "开学季", "standard", pending)
    assert failed["ok"] is False
    assert state.assets
    succeeded = generate_script(state, "开学季", "standard", pending)
    assert succeeded["ok"] is True
    script_steps = [step for step in state.steps if step["tool"] == "generate_script"]
    assert script_steps[0]["status"] == "failed"
    assert script_steps[1]["status"] == "succeeded"
    assert state.assets[0]["title"]


def test_repeat_save_does_not_double_charge(tmp_path):
    table = load_cost_table()
    assets = load_assets()
    path = tmp_path / "plans.json"
    first_state = make_state(budget=15)
    first = save_after_ready(first_state, PlanStore(path), table, assets, "standard")
    second_state = make_state(budget=15)
    second = save_after_ready(second_state, PlanStore(path), table, assets, "standard")
    assert first["created"] is True
    assert second["created"] is False
    assert first["plan"]["plan_id"] == second["plan"]["plan_id"]
    result = build_result(second_state, [], "", {"theme": "开学季"})
    assert result["total_cost"] == 12
    assert result["plan_reused"] is True


def save_after_ready(state, store, table, assets, variant):
    search_assets(state, assets, state.theme)
    generate_script(state, state.theme, variant)
    estimate_cost(state, table, variant)
    return save_plan(state, store, table, variant)


def test_deadline_too_soon_blocks_save(tmp_path):
    state = make_state(budget=15, deadline="2026-11-10", today="2026-11-09")
    table = load_cost_table()
    assets = load_assets()
    search_assets(state, assets, "开学季")
    generate_script(state, "开学季", "standard")
    estimated = estimate_cost(state, table, "standard")
    assert estimated["over_budget"] is False
    assert estimated["over_deadline"] is True
    saved = save_plan(state, _store(tmp_path), table, "standard")
    assert saved["ok"] is False


def test_equal_budget_and_equal_days_are_feasible():
    state = make_state(budget=12, deadline="2026-11-14", today="2026-11-09")
    table = {
        "unit": "模拟单位",
        "variant_order": ["standard"],
        "variants": {
            "standard": {
                "label": "标准方案",
                "cost": 12,
                "duration_days": 5,
                "line_items": [{"name": "制作", "amount": 12}],
            }
        },
    }
    state.variant_names = ["standard"]
    state.assets = [{"title": "素材"}]
    state.scripts["standard"] = "脚本"
    estimated = estimate_cost(state, table, "standard")
    assert estimated["over_budget"] is False
    assert estimated["over_deadline"] is False


def test_tool_call_limit_keeps_earlier_success():
    state = make_state(max_steps=2)
    assets = load_assets()
    table = load_cost_table()
    assert search_assets(state, assets, "开学季")["ok"] is True
    assert generate_script(state, "开学季", "standard")["ok"] is True
    blocked = estimate_cost(state, table, "standard")
    assert blocked["ok"] is False
    assert blocked["stop"] is True
    assert state.hit_step_limit is True
    assert state.tool_calls == 2
    assert state.steps[0]["status"] == "succeeded"
    assert state.steps[1]["status"] == "succeeded"
    assert state.steps[2]["status"] == "failed"
    result = build_result(state, [], "", {"theme": "开学季", "max_steps": 2})
    assert result["status"] == "incomplete"
    assert result["reason"] == "max_steps"


def test_both_variants_over_budget_report_gap(tmp_path):
    state = make_state(budget=5)
    table = load_cost_table()
    assets = load_assets()
    search_assets(state, assets, "开学季")
    for variant in ("standard", "lean"):
        generate_script(state, "开学季", variant)
        estimate_cost(state, table, variant)
        assert save_plan(state, _store(tmp_path), table, variant)["ok"] is False
    result = build_result(state, [], "", {"theme": "开学季", "budget": 5})
    assert result["status"] == "incomplete"
    assert result["reason"] == "no_feasible_plan"
    assert result["saved_plan"] is None
    assert result["gap"]["budget_shortfall"] == 3
    assert result["total_cost"] == 0
