"""读取合成素材和费用表。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DEFAULT_PLAN_STORE = ROOT / "output" / "plans.json"


def load_json(name: str):
    path = DATA_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


def load_assets() -> list[dict]:
    return load_json("assets.json")


def load_cost_table() -> dict:
    return load_json("cost_table.json")
