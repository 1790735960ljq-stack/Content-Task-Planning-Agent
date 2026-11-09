# 内容任务规划 Agent

帮助运营人员在预算和截止时间内完成一份内容活动方案。运行方式是 CLI：一次传入主题、预算和截止时间，进程内由模型调用工具，结束时打印 JSON。

## 评审入口

1. 看 [docs/01-requirements.md](docs/01-requirements.md) 到 [docs/05-retrospective.md](docs/05-retrospective.md)。
2. 看 [examples/](examples/) 里的真实运行记录。决策证据以 `model_mode` 为 `live` 且 `proves_model_decision` 为 `true` 的记录为准。
3. 复现命令见下文。业务上的未完成也会正常退出，并在 JSON 里给出 `reason`。

## 环境

- Python 3.11
- 依赖见 [requirements.txt](requirements.txt)
- 模型：阿里云 MaaS 的 OpenAI 兼容接口，本机验证模型为 `deepseek-v4.1-flash-1017`

## 安装

在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

编辑 `.env`，填入 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`。不要提交 `.env`。没有密钥时，只能加 `--mode mock` 验证流程，不能用来证明模型决策。

## 运行

以下命令都在仓库根目录执行。`--plan-store` 让每次验收使用独立方案库。

正常完成。预算 15，标准方案 12，保存标准方案：

```powershell
.\.venv\Scripts\python.exe -m src.main --theme "开学季" --budget 15 --deadline 2026-12-31 --plan-store output\01-plans.json
```

缺少截止时间。不调用模型：

```powershell
.\.venv\Scripts\python.exe -m src.main --theme "开学季" --budget 10
```

缺少预算：

```powershell
.\.venv\Scripts\python.exe -m src.main --theme "开学季" --deadline 2026-12-31
```

工具失败。第一次生成脚本失败，成功的检索结果保留：

```powershell
.\.venv\Scripts\python.exe -m src.main --theme "社团招新" --budget 15 --deadline 2026-12-31 --fail-tool generate_script --plan-store output\03-plans.json
```

预算超限。预算 10，标准方案 12，精简方案 8：

```powershell
.\.venv\Scripts\python.exe -m src.main --theme "开学季" --budget 10 --deadline 2026-12-31 --plan-store output\04-plans.json
```

两种方案都不可行。预算 5：

```powershell
.\.venv\Scripts\python.exe -m src.main --theme "校园歌手" --budget 5 --deadline 2026-12-31 --plan-store output\05-plans.json
```

重复执行。同一方案库连续跑两次，第二次返回原编号，费用仍为 8：

```powershell
.\.venv\Scripts\python.exe -m src.main --theme "读书日" --budget 10 --deadline 2026-12-31 --plan-store output\06-plans.json
.\.venv\Scripts\python.exe -m src.main --theme "读书日" --budget 10 --deadline 2026-12-31 --plan-store output\06-plans.json
```

步数上限。这条使用 mock，只证明到达上限后返回未完成：

```powershell
.\.venv\Scripts\python.exe -m src.main --theme "开学季" --budget 15 --deadline 2026-12-31 --mode mock --max-steps 2 --plan-store output\07-plans.json
```

加上 `--example-dir some-dir` 会把 `input.json`、`output.json`、`trace.json` 写到该目录。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

当前结果：18 项通过。测试不调用真实模型。费用、失败留痕、重复保存和步数上限由单元测试覆盖；Mock 循环只验证工具是否按返回结果继续，文档不把它当作决策成绩。

## 已记录的运行

| 目录 | 场景 | 模型 | 结果 |
|---|---|---|---|
| [examples/01-normal](examples/01-normal) | 正常完成 | live | 保存标准方案，费用 12 |
| [examples/02-missing-budget](examples/02-missing-budget) | 缺预算 | 未调用 | `missing_budget` |
| [examples/02-missing-deadline](examples/02-missing-deadline) | 缺截止时间 | 未调用 | `missing_deadline` |
| [examples/03-tool-failure](examples/03-tool-failure) | 脚本第一次失败 | live | 失败记录保留，检索结果保留，随后保存 |
| [examples/04-budget-overrun](examples/04-budget-overrun) | 预算 10 | live | 看到 12 后改选 8 并保存 |
| [examples/05-no-feasible](examples/05-no-feasible) | 预算 5 | live | 未保存，预算缺口 3 |
| [examples/06-repeat-first](examples/06-repeat-first) | 第一次执行 | live | 新建 `plan-0001`，费用 8 |
| [examples/06-repeat-second](examples/06-repeat-second) | 同一输入再执行 | live | 返回 `plan-0001`，费用仍为 8 |
| [examples/07-step-limit](examples/07-step-limit) | 工具调用上限 | mock | `max_steps`，不证明决策 |

这些记录生成于 2026-11-09，输出中的 `today` 为 `2026-11-09`。
