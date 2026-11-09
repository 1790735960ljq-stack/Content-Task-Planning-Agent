"""命令行入口。一次传入参数，结束时打印 JSON。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import run_task


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="内容任务规划 Agent")
    parser.add_argument("--theme", required=True, help="活动主题")
    parser.add_argument("--budget", type=float, default=None, help="预算，单位为模拟单位")
    parser.add_argument("--deadline", default=None, help="截止时间，格式 YYYY-MM-DD")
    parser.add_argument(
        "--fail-tool",
        default=None,
        choices=["search_assets", "generate_script", "estimate_cost", "save_plan"],
        help="让指定工具的第一次调用失败",
    )
    parser.add_argument("--max-steps", type=int, default=8, help="工具调用次数上限")
    parser.add_argument("--mode", choices=["live", "mock"], default=None, help="live 为真实模型，mock 只验证流程")
    parser.add_argument("--plan-store", default=None, help="方案库 JSON 路径，用于重复执行")
    parser.add_argument("--example-dir", default=None, help="把 input、output、trace 写到这个目录")
    return parser


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    result = run_task(
        theme=args.theme,
        budget=args.budget,
        deadline=args.deadline,
        mode=args.mode,
        fail_tool=args.fail_tool,
        max_steps=args.max_steps,
        plan_store_path=Path(args.plan_store) if args.plan_store else None,
    )
    if args.example_dir:
        directory = Path(args.example_dir)
        write_json(directory / "input.json", result["input"])
        write_json(directory / "output.json", result)
        write_json(directory / "trace.json", result["trace"])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("reason") in {"model_unavailable", "model_call_failed"}:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
