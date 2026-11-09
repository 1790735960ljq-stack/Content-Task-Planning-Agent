"""从模型消息中摘出可核对的 Trace。不补写思维过程。"""

from __future__ import annotations

import json

from .messages import message_text


def _tool_calls(message) -> list[dict]:
    calls = getattr(message, "tool_calls", None) or []
    normalized = []
    for call in calls:
        if isinstance(call, dict):
            args = call.get("args")
            if args is None:
                args = call.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"raw": args}
            normalized.append(
                {
                    "name": call.get("name"),
                    "args": args,
                    "id": call.get("id"),
                }
            )
    return normalized


def _summarize_tool_message(message) -> str:
    if message is None:
        return "无工具返回"
    text = message_text(message)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text[:200]
    if isinstance(payload, dict):
        if payload.get("summary"):
            return str(payload["summary"])
        if payload.get("error"):
            return str(payload["error"])
    return text[:200]


def _format_action(call: dict | None) -> str:
    if call is None:
        return "结束"
    args = call.get("args") or {}
    rendered = ", ".join(f"{key}={value}" for key, value in args.items())
    if rendered:
        return f"{call['name']}({rendered})"
    return str(call.get("name"))


def _next_action(messages, start_index: int) -> str:
    for message in messages[start_index + 1 :]:
        calls = _tool_calls(message)
        if calls:
            return _format_action(calls[0])
    return "结束"


def _previous_summary(messages, start_index: int) -> str:
    for message in reversed(messages[:start_index]):
        if getattr(message, "tool_call_id", None):
            return _summarize_tool_message(message)
    return "尚无工具结果"


def build_trace(messages) -> list[dict]:
    trace = []
    for index, message in enumerate(messages):
        calls = _tool_calls(message)
        if not calls:
            continue
        reason = message_text(message).strip()
        for call in calls:
            tool_message = None
            for candidate in messages[index + 1 :]:
                if getattr(candidate, "tool_call_id", None) == call.get("id"):
                    tool_message = candidate
                    break
            summary = _summarize_tool_message(tool_message)
            if not reason:
                reason = f"上一步结果：{_previous_summary(messages, index)}；本次选择 {call['name']}"
            trace.append(
                {
                    "step": len(trace) + 1,
                    "tool": call["name"],
                    "args": call["args"],
                    "result_summary": summary,
                    "next_action": _next_action(messages, index),
                    "reason": reason,
                }
            )
    return trace


def final_text(messages) -> str:
    text = ""
    for message in messages:
        if _tool_calls(message):
            continue
        if getattr(message, "type", None) == "ai" or message.__class__.__name__.startswith("AIMessage"):
            content = message_text(message).strip()
            if content:
                text = content
    return text
