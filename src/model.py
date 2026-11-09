"""真实模型与离线流程 Mock。

FlowMockModel 按工具结果选择下一步，只用于没有真实模型时的流程验证。
它里面的分支不是考核要求的模型决策。
"""

from __future__ import annotations

import json
import os

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from .messages import message_text


class ModelConfigError(RuntimeError):
    """真实模型缺少配置。"""


def _safe_text(text: str) -> str:
    secret = os.getenv("LLM_API_KEY", "")
    if secret:
        text = text.replace(secret, "***")
    return text[:500]


def build_chat_model():
    key = os.getenv("LLM_API_KEY", "").strip()
    base_url = os.getenv("LLM_BASE_URL", "").strip()
    model_name = os.getenv("LLM_MODEL", "").strip()
    if not key or not base_url or not model_name:
        raise ModelConfigError("缺少 LLM_API_KEY、LLM_BASE_URL 或 LLM_MODEL")
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model_name,
        api_key=key,
        base_url=base_url,
        temperature=0,
        timeout=120,
        max_retries=1,
    )


def live_model_name() -> str:
    return os.getenv("LLM_MODEL", "").strip() or "unknown"


def _tool_payloads(messages) -> list[dict]:
    payloads = []
    for message in messages:
        if getattr(message, "type", None) != "tool" and message.__class__.__name__ != "ToolMessage":
            continue
        text = message_text(message)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {"raw": text}
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _theme_from_messages(messages) -> str:
    for message in messages:
        for line in message_text(message).splitlines():
            if line.startswith("主题："):
                return line.split("主题：", 1)[1].strip()
    return ""


def _call(name: str, args: dict, reason: str) -> dict:
    return {"name": name, "args": args, "reason": reason}


def choose_mock_action(messages) -> dict | None:
    """脚本化流程。只读工具结果，不代替真实模型提交决策证据。"""
    results = _tool_payloads(messages)
    theme = _theme_from_messages(messages)
    if results and results[-1].get("stop"):
        return None
    last = results[-1] if results else None
    if last and last.get("retryable"):
        tool_name = last.get("tool")
        if tool_name == "search_assets":
            return _call("search_assets", {"theme": theme}, "检索失败，再试一次。已成功的结果会保留。")
        if tool_name == "generate_script":
            variant = last.get("variant") or "standard"
            return _call(
                "generate_script",
                {"theme": theme, "variant": variant},
                "脚本生成失败，不把该步记为成功，再试一次。",
            )
        if tool_name == "estimate_cost":
            return _call(
                "estimate_cost",
                {"variant": last.get("variant") or "standard"},
                "估算失败，再试一次。",
            )
        if tool_name == "save_plan":
            return _call(
                "save_plan",
                {"variant": last.get("variant") or "standard"},
                "保存失败，再试一次。",
            )

    def succeeded(tool_name: str) -> list[dict]:
        return [item for item in results if item.get("ok") and item.get("tool") == tool_name]

    searches = succeeded("search_assets")
    scripts = {item.get("variant"): item for item in succeeded("generate_script")}
    estimates = {item.get("variant"): item for item in succeeded("estimate_cost")}
    saves = succeeded("save_plan")
    if not searches:
        return _call("search_assets", {"theme": theme}, "还没有素材，先检索。")
    if "standard" not in scripts:
        return _call(
            "generate_script",
            {"theme": theme, "variant": "standard"},
            "按执行骨架先生成标准方案脚本。",
        )
    if "standard" not in estimates:
        return _call("estimate_cost", {"variant": "standard"}, "标准脚本已生成，估算费用和耗时。")
    standard = estimates["standard"]
    standard_ok = not standard.get("over_budget") and not standard.get("over_deadline")
    if standard_ok and not saves:
        return _call("save_plan", {"variant": "standard"}, "标准方案未超预算且未超期，保存。")
    if not standard_ok and "lean" not in scripts:
        return _call(
            "generate_script",
            {"theme": theme, "variant": "lean"},
            "标准方案超出限制，改为生成精简方案。",
        )
    if not standard_ok and "lean" not in estimates:
        return _call("estimate_cost", {"variant": "lean"}, "精简脚本已有，重新估算。")
    if "lean" in estimates:
        lean = estimates["lean"]
        lean_ok = not lean.get("over_budget") and not lean.get("over_deadline")
        if lean_ok and not saves:
            return _call("save_plan", {"variant": "lean"}, "精简方案满足预算和截止时间，保存。")
    return None


class FlowMockModel(BaseChatModel):
    """离线脚本模型。输出必须带 model_mode=mock，不能当作真实决策证据。"""

    call_index: int = 0

    @property
    def _llm_type(self) -> str:
        return "flow-mock"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        action = choose_mock_action(messages)
        if action is None:
            message = AIMessage(content="没有需要继续调用的工具，结束规划。")
        else:
            self.call_index += 1
            message = AIMessage(
                content=action["reason"],
                tool_calls=[
                    {
                        "name": action["name"],
                        "args": action["args"],
                        "id": f"mock-{self.call_index}",
                        "type": "tool_call",
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])
