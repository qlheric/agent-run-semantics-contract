# Agent Run 语义契约 v0.1

> **本文只定形状、不定实现。**
> 单一真源 = `contract/run_semantics.schema.json`。Java 与 Python 两边各按它实现/校验，**不各写一套**；
> 双端一致性由 `contract/fixtures/` + 同一组 `contract_check` 命令**逐字节比对**证明（协议见 `CONFORMANCE.md`）。
> 版本 `0.1.0`；契约 ID 永不重命名，版本变了还是同一契约。
>
> **本 schema 描述的是「run 快照文档」**（`termination` / `error` / `audit` / `tools` 都是快照字段），
> **不是 HTTP 请求体**；请求体形状在 TASK-001 另定。

## 0. 怎么用这份契约

| 你要做的事 | 看哪节 |
|---|---|
| 想知道某个字段是什么意思 | §1 十八项字段表 |
| 想知道语义由谁裁决、失败了算谁的 | §2 六条语义 |
| 要算 `args_digest` / 要对齐 canonical | §3 `args_digest` 小节 |
| 要改契约 | §4 演进规则 |
| 要做双端一致性比对 | `CONFORMANCE.md` |

**纪律速记（三条最容易犯的错）**

1. `additionalProperties: false` 不许只写顶层 —— 嵌套的每一层 object 都要写；
2. 金额一律 `*_usd` + `type: integer`（单位 micro-USD），**禁浮点**；
3. `x-status-transitions` 的 key 与 value 都必须落在 `run.status` 枚举内。

> **纪律① 的唯一例外**：`$defs/json_value` 里标了 `x-free-form` 的 object 分支（args 载荷容器）
> 用 `additionalProperties: {$ref: json_value}` 而不是 `false` ——
> 理由：**args 是自由 JSON 载荷**，字段名不可枚举，禁掉就没法表达任意工具参数。
> 这条豁免只在 args 载荷容器上生效，别处一律 `additionalProperties: false`。

**权威状态转移表 = `status-transitions.json`**（v0.1，机器可读，9 状态全覆盖 + 每条边）。
`schema.json` 只定**形状**（`status_targets`），不承载边；`fixtures/*.json` 根级的 `x-status-transitions` 只是**场景样例**（9 份互不相同），**任何消费方都不得拿某份 fixture 当权威**。
M1 控制面启动时读本文件并用 `run.status` 枚举自检；契约修订时只改这一处。

## 1. 十八项必进字段

| # | 字段 | 类型 | 必填 | 一句话语义 | 版本 |
|---|---|---|---|---|---|
| 1 | `spec.version` | string（`const "0.1.0"`） | 是 | 契约版本，与字段演进解耦 | v0.1 |
| 2 | `run.status` + 根级 `x-status-transitions` | string 枚举(9) + object | 是 | run 状态机与合法转移表（转移表是**契约级元数据**，挂在文档根，不塞进每份 run 快照）；`outcome_unknown` = 副作用可能已发生 | v0.1 |
| 3 | `step.attempt` / `run.policy.max_attempts` | integer | 是 | 第几次尝试 / 重试上限 | v0.1 |
| 4 | `step.granted.deadline_at_ms` / `step.granted.deadline_ms` / `step.granted.clock` | integer / integer / enum | 是 | 绝对墙钟截止 + 本 step 时长上限 + 时钟约定（`wall`/`monotonic`） | v0.1 |
| 5 | `step.idempotency_scope` | string \| null | 是（可为 null） | 控制面分配的幂等范围（格式 `run_id/step_id/tool`），生命周期绑定 run | v0.1 |
| 6 | `run.budget.max_steps` / `max_tool_calls` / `max_tokens` / `max_cost_usd` | integer | 是 | 四项配额（金额单位 micro-USD，`reserve → settle` 两段扣费） | v0.1 |
| 7 | `step.granted.quota` + `run.budget.used_*` | integer | 否（授予时必给） | `sum(used) + granted ≤ quota` 由控制面在同一原子操作内判定；执行面只有额度，不是所有权 | v0.1 |
| 8 | `run.termination.reason` + `run.error.code` | enum / enum | 是 | 终止原因 / 错误码 | v0.1 |
| 9 | `run.audit[]` | array\<object\> | 是（可为空数组） | 审计条目 `{seq, ts, type, run_id, step_id, attempt, payload, prev_hash, hash}`，append-only 哈希链 | v0.1 |
| 10 | `run.tools[]` | array\<object\> | 是（可为空数组） | 工具目录条目 `{tool, tier, args_schema, side_effect, idempotency_key_template, result_budget_bytes}`，按档过滤后才下发 | v0.1 |
| 11 | `step.approval.status` | enum(4) | 是 | 审批状态 `pending/approved/rejected/expired` | v0.1 |
| 12 | `step.args` / `step.args_ref` | object \| string | 二选一 | 参数本体或大参数引用；`oneOf` 严格二选一（同时给或都不给都非法） | v0.1 |
| 13 | `step.side_effect` | enum(3) | 是 | 声明式副作用 `none/idempotent/non_idempotent`，控制面据此决定是否允许重试 | v0.1 |
| 14 | `step.idempotency_key` | string | 是 | **业务侧**幂等键（如支付单号）；与 #5 的控制面内部去重是两个东西 | v0.1 |
| 15 | `run.policy.retry_profile` | object | 是 | 可执行细化：档位名 `name` + `max_attempts` + `backoff_ms` + `retryable_tools` | v0.1 |
| 16 | `run.budget.currency` + `run.budget.price_snapshot_id` | string(3 位大写) / string | 是 | 成本口径必须指到一份价格快照，否则「成本」不可核 | v0.1 |
| 17 | `run.clock` / `spec.clock` | enum(2) | 是 | 时间注入点（`wall`/`monotonic`），否则 deadline 不可测 | v0.1 |
| 18 | `step.grant_request` | object | 是 | `request_grant` 入参契约 `{run_id, step_id, attempt, idempotency_scope, tool, args/args_ref}`；控制面算 `args_digest` 并随 grant 回传，执行面复算校验 | v0.1 |

### 1.1 v0.2 字段（不阻塞 M1，不进 required）

| 字段 | 语义 |
|---|---|
| `run.parent_run_id` | 降级/重试链的父 run |
| `run.tenant_quota_ref` | 租户配额引用 |
| 工具目录分页 | 大目录按页下发 |
| `step.result_digest` | 结果摘要摘要值 |
| `run.labels` | 自由标签 |
| 授权租约 | 见方案 §3.5，M2 落地 |

> 这些字段在 v0.1 **一律不在 schema 里**（`additionalProperties: false` 会拒掉）。
> 引入时按 §4 演进规则 bump minor。

### 1.2 枚举全表（v0.1）

| 枚举 | 取值 |
|---|---|
| `run.status` | `created` `running` `waiting_approval` `paused` `degraded` `succeeded` `failed` `cancelled` `outcome_unknown` |
| `spec.framework` | `langgraph` `agent_framework` `custom` |
| `run.policy.capability_tier` | `read` `write` `destructive` |
| `run.policy.retry_profile.name` | `model_only` `read_only` `full` |
| `step.side_effect` / `run.tools[].side_effect` | `none` `idempotent` `non_idempotent` |
| `step.approval.status` | `pending` `approved` `rejected` `expired` |
| `clock`（`spec.clock`/`run.clock`/`granted.clock`） | `wall` `monotonic` |
| `run.tools[].tier` | `read` `write` `destructive` |
| `run.termination.reason` | `completed` `tool_error` `budget_exhausted` `deadline_exceeded` `approval_denied` `cancelled_by_caller` `degraded_circuit_break` |
| `run.error.code` | `none` `tool_failed` `budget_exhausted` `deadline_exceeded` `approval_denied` `policy_violation` `internal_error` |

## 2. 六条语义（每条写明「谁裁决」与「失败语义」）

| # | 语义 | 规则 | **谁裁决** | **失败语义** |
|---|---|---|---|---|
| 1 | **幂等** | run 级 `run.idempotency_key` + step 级 `step.idempotency_scope`（同 scope 内不重复执行副作用）。重放同 key ⇒ 同一 run，不重复扣费、不重复副作用 | **控制面**（原子存储） | scope 相同但 `args_digest` 不同 ⇒ **拒绝并记审计**，不覆盖；scope 已被执行过 ⇒ `request_grant` 返回 `skip` + 上次结果摘要，工具不调用 |
| 2 | **截止时间** | run 总 deadline 逐 step 下发剩余（`granted.deadline_at_ms` + `granted.deadline_ms`）；执行面用本地单调钟计时但**不自行宣布超时**，只上报「已超授予时长」 | **控制面** | 超时 ⇒ run 落 `failed` + `termination.reason=deadline_exceeded` + `error.code=deadline_exceeded`；执行面自报超时**不被承认** |
| 3 | **分级重试** | 三档 `model_only` / `read_only` / `full`；是否可重试由 `step.side_effect` + `retry_profile` **共同决定**，非幂等副作用不自动重试；模型调用可重试（无外部副作用） | **控制面** | 超出 `max_attempts` ⇒ 落 `failed`（`error.code=tool_failed`）；非幂等副作用在失败瞬间 ⇒ 见语义 6 的 `outcome_unknown`，**不是** `failed` |
| 4 | **预算与降级** | 四项配额逐 step `reserve → settle`；重试**不二次预扣**但必须 settle 实际用量，差额退回；超限优先级写死 `max_steps → max_tool_calls → max_tokens → max_cost`（先砍数量维度，成本最后砍）；先降级后熔断 | **控制面**（`sum(used) + granted ≤ quota` 同一原子操作） | 降级到底仍超限 ⇒ run 落 `failed` + `termination.reason=budget_exhausted` + `error.code=budget_exhausted`；降级必须留痕（审计链可读出降级前后策略） |
| 5 | **权限分档** | `read` / `write` / `destructive` + 审批档；run 级能力上限写 `run.policy.capability_tier`（同样是三档），工具目录按档过滤后下发（MCP 策略代理）——不能做的工具根本不下发 | **控制面**（策略执行器 PEP） | 越权调用 ⇒ 工具不执行；`step.approval.status=rejected` ⇒ 落 `failed` + `termination.reason=approval_denied` + `error.code=approval_denied`；审批过期 ⇒ 同上（`status=expired`） |
| 6 | **审计** | 哈希链 append-only（run/step/tool 级），可重放校验 | **控制面** | 断链 ⇒ **报**（校验失败即报，不静默修复）；链断 ⇒ 恢复流程拒绝继续，恢复前先查幂等存储 |

### 2.1 非幂等副作用失败的交代（第七条，语义 3/6 的延伸）

非幂等副作用失败时：

* run 状态落 **`outcome_unknown`（不是 `failed`）**，并带 `side_effect_unknown = true`；
* 审计记 attempt / `args_digest` / 工具返回 / 终止原因；
* **向调用方返回「结果未知，请查询副作用状态」而不是「失败」** ——
  因为非幂等副作用失败时，调用方最危险的动作就是直接重试；
* 恢复时**先查幂等存储**，再决定要不要重放。

> 裁决者：**控制面**。执行面只上报，不自行宣布「已失败可重试」。

## 3. `args_digest` 规范化算法

算错幂等全错。契约写死（方案 §4.3）：

1. **禁止浮点**：`args` 只允许 `string` / `integer` / `boolean` / `null` / `array` / `object`。
   金额整数 micro-USD、token 整数、耗时整数 ms。
   **这条直接消掉浮点规范化难题**（不必实现 ES6 最短浮点表示）。
   算法实现遇到浮点 ⇒ **直接报错退出**（Python 侧 exit 2），**不许**把它转义成 `\uXXXX` 充数。
2. **对象键按码位序排序**；**数组顺序参与**（数组有序）；**null 值的键剔除**（未传 ≠ 传 null）；**Unicode 归一化取 NFC**。
3. `sha256(canonical_json)`，`canonical_json` = `json.dumps(sort_keys=True, separators=(',', ':'), ensure_ascii=False)` 的 UTF-8 字节。
   **Java 与 Python 各写一份实现，用同一组 fixture 逐字节比对。**

### 3.1 两个完整样例（与 `--digest` 实跑一致）

**样例 A** —— `contract/fixtures/valid/step_full.json`

输入 `step.args`：

```json
{
  "query": "中文参数：控制面契约",
  "top_k": 5,
  "recursive": false,
  "filter": null,
  "tags": ["契约", "v0.1"],
  "nested": { "b": 2, "a": 1 }
}
```

规范化（`filter` 是 null ⇒ 剔除；`nested` 的键按码位序重排；中文不转义）：

```
canonical: {"nested":{"a":1,"b":2},"query":"中文参数：控制面契约","recursive":false,"tags":["契约","v0.1"],"top_k":5}
sha256: d45b61e0549b63d28f2579042d2bcb2275fae91fe45b603691b7af42072986fd
```

复现命令：

```bash
python tools/contract_check.py --digest contract/fixtures/valid/step_full.json
```

**样例 B** —— `contract/fixtures/valid/step_args_ref.json`

输入 `step.args_ref`（字符串；引用内容不解析）：

```
objstore://m1/run_args_ref_002/step_0/args.json
```

规范化（字符串原样 NFC 归一化后 JSON 编码）：

```
canonical: "objstore://m1/run_args_ref_002/step_0/args.json"
sha256: da6ef4028ce3b8ce61c138e06ae11df7c8926bc2d6c1003787b0010e9409a859
```

复现命令：

```bash
python tools/contract_check.py --digest contract/fixtures/valid/step_args_ref.json
```

## 4. 演进规则（6 条）

1. **契约 ID 是契约，永不重命名**；ID 变了 = 换契约。**版本标在 `spec.version`**；版本变了 = 还是同一契约。
2. 新增字段向后兼容；删除或改语义 = **major**。
3. **JSON Schema 一律 `additionalProperties: false`**（含嵌套每一层）——
   新增字段必须 bump **minor**，否则旧端静默丢弃新字段。
4. **枚举只能增不能删**（删枚举值 = major）；每个枚举值在 Java/Python 两端必须有实现，
   **CI 自动检查「schema 枚举集合 ⊆ 两端实现集合」**。
5. **字段重命名 = 新字段 + ≥1 个 minor 的废弃期**（「ID 永不重命名」只覆盖枚举值，不覆盖结构体字段）。
6. **双端实现清单进 CI**：契约变更 ⇒ 双端实现清单必须同步更新，且 conformance 逐字节比对必须仍绿。

## 5. 本契约明确**不做**什么

* 不规定实现语言、不规定框架、不规定存储形态；
* 不规定审批流的具体交互（只定 `approval` 的四态形状）；
* 不规定审计链哈希的具体算法（只定字段形状与 append-only 语义）；
* 不接 LLM，不含任何提示词/模型身份相关内容。

> 再次强调：**本文只定形状、不定实现。**
