# 评估

测试命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

2026-11-09 的结果是 18 项通过。这些测试使用本地函数和流程 Mock，不证明真实模型会改计划。决策证据是下面的 live 记录。

## 正常完成

[examples/01-normal](../examples/01-normal)

预算 15，截止 2026-12-31。模型检索素材、生成标准脚本、估算出 12 且未超期，然后保存。`total_cost` 为 12，方案类型是 `standard`。

## 信息不足

[examples/02-missing-budget](../examples/02-missing-budget) 的原因是 `missing_budget`。

[examples/02-missing-deadline](../examples/02-missing-deadline) 的原因是 `missing_deadline`。

两组都没有工具调用，任务计划四个阶段都是 `pending`。

## 工具失败和部分成功

[examples/03-tool-failure](../examples/03-tool-failure)

`--fail-tool generate_script` 让第一次生成失败。Trace 第 2 步返回“脚本生成服务暂时失败”，第 3 步模型写明要重试同一方案，并再次调用 `generate_script`。步骤日志里第一次仍是 `failed`，任务计划该阶段为 `succeeded_after_failure`，失败尝试还在 `attempts` 里。检索到的“新生采访”在失败后仍然保留。随后估算 12，未超预算，保存成功。

## 预算超限

[examples/04-budget-overrun](../examples/04-budget-overrun)

预算 10。标准方案估算 12，超出 2。模型的下一动作是 `generate_script(theme=开学季, variant=lean)`，依据是“比预算多 2 个，不保存，改为生成精简方案”。精简方案估算 8，未超预算，保存为 `plan-0001`。`total_cost` 为 8，不是 20。

## 没有可行方案

[examples/05-no-feasible](../examples/05-no-feasible)

预算 5。标准方案超出 7，精简方案超出 3。模型估算完精简方案后停止，没有调用保存。状态是 `incomplete`，原因是 `no_feasible_plan`，`budget_shortfall` 为 3，`total_cost` 为 0。

这次检索没有“校园歌手”专用素材，工具返回了通用素材。模型仍然继续估算，并因为预算停止。素材不足没有被记成方案已完成。

## 重复执行

[examples/06-repeat-first](../examples/06-repeat-first) 新建 `plan-0001`，费用 8。

[examples/06-repeat-second](../examples/06-repeat-second) 使用同一个方案库再次运行。保存结果是“该方案已存在，返回原编号 plan-0001，费用仍为 8，不重复计费”。`plan_reused` 为 `true`，`total_cost` 仍为 8。

## 步数上限

[examples/07-step-limit](../examples/07-step-limit)

这组是 mock，`proves_model_decision` 为 `false`。上限设为 2。前两步成功，第三次估算被拒绝，原因是达到工具调用上限，最终状态为 `max_steps`。已成功的检索和脚本还在。

对应测试是 [tests/test_agent_flow.py](../tests/test_agent_flow.py) 的 `test_mock_step_limit`，以及 [tests/test_state_and_tools.py](../tests/test_state_and_tools.py) 的费用、拒绝保存和失败留痕测试。

## 失败和限制

- 失败步骤的跨进程续跑没有做。一次运行内的失败记录和成功结果可以保留。
- 流程 Mock 含有固定分支，只能证明工具链能走通。
- 方案去重键是主题加方案类型。同一主题、同一类型再次执行不会生成第二个方案，也不会改写第一次保存的脚本。
- 截止时间用运行当天计算。本组记录的当天是 2026-11-09，距离 2026-12-31 有 52 天，所以这些用例没有触发超期。超期拒绝由单元测试覆盖。
- 真实模型本次按预期改了计划，但不保证每次措辞相同。费用和能否保存由工具结果决定，不由模型自己报数。
