"""ReAct 循环测试。Mock 只证明流程，不断言真实模型决策。"""

import json
from datetime import date

from src.agent import run_task
from src.trace import build_trace


class Msg:
    def __init__(self, content, tool_calls=None, tool_call_id=None, message_type="ai"):
        self.content = content
        self.tool_calls = tool_calls or []
        self.tool_call_id = tool_call_id
        self.type = message_type


def test_trace_records_args_result_and_next_action():
    messages = [
        Msg("主题：开学季", message_type="human"),
        Msg(
            "先估算标准方案。",
            tool_calls=[{"name": "estimate_cost", "args": {"variant": "standard"}, "id": "c1"}],
        ),
        Msg(
            json.dumps({"ok": True, "summary": "估算 12，超出预算 2"}, ensure_ascii=False),
            tool_call_id="c1",
            message_type="tool",
        ),
        Msg(
            "估算超出预算，改估算精简方案。",
            tool_calls=[{"name": "estimate_cost", "args": {"variant": "lean"}, "id": "c2"}],
        ),
        Msg(
            json.dumps({"ok": True, "summary": "估算 8，未超预算"}, ensure_ascii=False),
            tool_call_id="c2",
            message_type="tool",
        ),
        Msg("精简方案可以保存。", message_type="ai"),
    ]
    trace = build_trace(messages)
    assert trace[0]["args"] == {"variant": "standard"}
    assert "超出预算" in trace[0]["result_summary"]
    assert trace[0]["next_action"] == "estimate_cost(variant=lean)"
    assert trace[0]["reason"] == "先估算标准方案。"
    assert trace[1]["next_action"] == "结束"


def test_missing_budget_and_deadline_stop_before_tools(tmp_path):
    missing_budget = run_task(
        "开学季",
        None,
        "2026-12-31",
        plan_store_path=tmp_path / "a.json",
        mode="mock",
    )
    missing_deadline = run_task(
        "开学季",
        10,
        None,
        plan_store_path=tmp_path / "b.json",
        mode="mock",
    )
    assert missing_budget["reason"] == "missing_budget"
    assert missing_budget["trace"] == []
    assert missing_budget["tool_calls"] == 0
    assert missing_deadline["reason"] == "missing_deadline"
    assert all(stage["status"] == "pending" for stage in missing_deadline["task_plan"])


def test_mock_normal_saves_standard(tmp_path):
    result = run_task(
        "开学季",
        15,
        "2026-12-31",
        mode="mock",
        today=date(2026, 11, 9),
        plan_store_path=tmp_path / "plans.json",
    )
    assert result["model_mode"] == "mock"
    assert result["proves_model_decision"] is False
    assert result["status"] == "completed"
    assert result["saved_plan"]["variant"] == "standard"
    assert result["total_cost"] == 12


def test_mock_switches_after_over_budget(tmp_path):
    result = run_task(
        "开学季",
        10,
        "2026-12-31",
        mode="mock",
        today=date(2026, 11, 9),
        plan_store_path=tmp_path / "plans.json",
    )
    assert result["proves_model_decision"] is False
    assert result["status"] == "completed"
    assert result["tool_results"]["estimates"]["standard"]["cost"] == 12
    assert result["tool_results"]["estimates"]["standard"]["over_budget"] is True
    assert result["saved_plan"]["variant"] == "lean"
    assert result["total_cost"] == 8
    standard_estimate = next(
        item
        for item in result["trace"]
        if item["tool"] == "estimate_cost" and item["args"].get("variant") == "standard"
    )
    assert "lean" in standard_estimate["next_action"]


def test_mock_keeps_failed_step_and_assets(tmp_path):
    result = run_task(
        "社团招新",
        15,
        "2026-12-31",
        mode="mock",
        fail_tool="generate_script",
        today=date(2026, 11, 9),
        plan_store_path=tmp_path / "plans.json",
    )
    assert result["status"] == "completed"
    assert result["tool_results"]["assets"]
    script_steps = [step for step in result["step_log"] if step["tool"] == "generate_script"]
    assert script_steps[0]["status"] == "failed"
    assert script_steps[1]["status"] == "succeeded"
    stage = next(item for item in result["task_plan"] if item["tool"] == "generate_script")
    assert stage["status"] == "succeeded_after_failure"
    assert stage["attempts"][0]["status"] == "failed"


def test_mock_no_feasible_plan(tmp_path):
    result = run_task(
        "校园歌手",
        5,
        "2026-12-31",
        mode="mock",
        today=date(2026, 11, 9),
        plan_store_path=tmp_path / "plans.json",
    )
    assert result["status"] == "incomplete"
    assert result["reason"] == "no_feasible_plan"
    assert result["saved_plan"] is None
    assert result["total_cost"] == 0
    assert result["gap"]["budget_shortfall"] == 3


def test_mock_repeat_returns_same_plan(tmp_path):
    path = tmp_path / "plans.json"
    kwargs = {
        "mode": "mock",
        "today": date(2026, 11, 9),
        "plan_store_path": path,
    }
    first = run_task("读书日", 10, "2026-12-31", **kwargs)
    second = run_task("读书日", 10, "2026-12-31", **kwargs)
    assert first["status"] == "completed"
    assert second["saved_plan"]["plan_id"] == first["saved_plan"]["plan_id"]
    assert second["plan_reused"] is True
    assert second["total_cost"] == 8


def test_mock_step_limit(tmp_path):
    result = run_task(
        "开学季",
        15,
        "2026-12-31",
        mode="mock",
        max_steps=2,
        today=date(2026, 11, 9),
        plan_store_path=tmp_path / "plans.json",
    )
    assert result["status"] == "incomplete"
    assert result["reason"] == "max_steps"
    assert result["saved_plan"] is None
    assert result["tool_calls"] == 2
    assert any("上限" in step["result_summary"] for step in result["step_log"])


def test_result_does_not_leak_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test-secret-value")
    result = run_task(
        "开学季",
        15,
        "2026-12-31",
        mode="mock",
        today=date(2026, 11, 9),
        plan_store_path=tmp_path / "plans.json",
    )
    assert "sk-test-secret-value" not in json.dumps(result, ensure_ascii=False)
