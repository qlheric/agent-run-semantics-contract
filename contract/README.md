# Agent Run 语义契约 v0.4.0

> **本文只定形状、不定实现。**
> 单一真源 = `contract/run_semantics.schema.json`。Java 与 Python 两边各按它实现/校验，**不各写一套**；
> 双端一致性由 `contract/fixtures/` + 同一组 `contract_check` 命令**逐字节比对**证明（协议见 `CONFORMANCE.md`）。
> 版本 `0.4.0`；契约 ID 永不重命名，版本变了还是同一契约。
>
> **本 schema 描述的是「run 快照文档」**（`termination` / `error` / `audit` / `tools` 都是快照字段），
> **不是 HTTP 请求体**；请求体形状在 TASK-001 另定。

## 0. 怎么用这份契约

| 你要做的事 | 看哪节 |
|---|---|
| 想知道某个字段是什么意思 | §1 语义分组表 |
| 想知道**当前必填字段与总数** | 跑 `python tools/contract_check.py --fields --count`（本文不写死数字） |
| 想知道语义由谁裁决、失败了算谁的 | §2 六条语义 |
| 要算 `args_digest` / 要对齐 canonical | §3 `args_digest` 小节 |
| 要**重放状态转移序列**（验「篡改转移 ⇒ 必被检出」） | §2.3 状态转移留痕与重放 + `python tools/contract_check.py --replay` |
| 要改契约 | §4 演进规则 |
| 要做双端一致性比对 | `CONFORMANCE.md` |

**纪律速记（五条最容易犯的错）**

1. `additionalProperties: false` 不许只写顶层 —— 嵌套的每一层 object 都要写；
2. **金额**一律 `*_usd` / `*_micro` + `type: integer`（单位 micro-USD），**禁浮点**；
   **其余字段（含 args）允许浮点**（v0.3 放开：LLM 参数如 `temperature=0.7` 必须能表达）；
3. `x-status-transitions` 的 key 与 value 都必须落在 `run.status` 枚举内；
4. **版本四处一致**（schema.title / spec.version / status-transitions.json / 本文）——
   校验器 `--schema` 自动断言，不一致即红；
5. **状态转移必须留痕**（v0.4）：每一次状态推进都要在审计里写一条
   `type="state_transition"` 的条目（`from`/`to`/`actor`/`cause`）——
   否则 `--replay` 重不出序列，「篡改转移 ⇒ 必被检出」这条性质就不成立。

> **纪律① 的唯一例外**：`$defs/json_value` 与 `$defs/extensions` 里标了 `x-free-form` 的
> object 分支（args 载荷容器、逃生舱）用 `additionalProperties: {$ref: json_value}` 而不是 `false` ——
> 理由：**args 是自由 JSON 载荷**，字段名不可枚举，禁掉就没法表达任意工具参数。
> 这条豁免只在 args 载荷容器与逃生舱上生效，别处一律 `additionalProperties: false`。

**权威状态转移表 = `status-transitions.json`**（v0.4.0，机器可读，9 状态全覆盖 + 每条边）。
`schema.json` 只定**形状**（`status_targets`），不承载边；`fixtures/*.json` 根级的 `x-status-transitions` 只是**场景样例**（**均对齐权威表**，见 CONFORMANCE §3.5 纪律④），**任何消费方都不得拿某份 fixture 当权威**。
M1 控制面启动时读本文件并用 `run.status` 枚举自检；契约修订时只改这一处。

### 0.1 终态 vs 准静态态（v0.4 新增，必读）

权威表里的「有出口 / 没出口」**不等于**「run 还活着 / 死了」。v0.4 起明确区分两类：

| 类别 | 状态 | 含义 | 能否离开 |
|---|---|---|---|
| **终态（terminal）** | `succeeded` / `failed` / `cancelled` | run 已经**结束**，没有出口 | **不能**（转移表里是空数组） |
| **准静态态（quiescent）** | `outcome_unknown` | run **没有结束**，但**不再自动推进** —— 副作用状态未知，等外部裁决 | **能，但每条出边都带 guard** |

`outcome_unknown` 的出边（v0.4）：`failed` / `succeeded` / `cancelled`，
**且离开时必须带外部对账证据**（审计 `state_transition` 条目的 `payload.evidence_ref`）——
没有证据就离开未知态 ⇒ `--replay` 判红（路径 `/run/audit/<i>/payload/evidence_ref`）。

> **v0.4 修掉的安全洞**：`outcome_unknown → running` 这条边被**删除**了。
> 未经对账就重入执行态 ⇒ 可能把已经落库的副作用**再做一遍**。
> 该边在 `status-transitions.json` 的 `forbidden` 段里被显式列入黑名单：
> 即使有人把它加回 `transitions`，校验器也会直接判红（不是「悄悄生效」）。

> **v0.4 变更摘要**（从 v0.1 起）：
> `step` 改**数组**；新增 `run.reservations`（reserve→settle 结构化留痕）；
> 审计时间戳改 `ts_ms`（epoch 毫秒整数，**进哈希**）；`args` **放开浮点**；
> 幂等概念 4→2（`run.idempotency_key` + `step.dedupe_scope`）；
> `run.error` 改可空（去掉 `code:"none"` 哨兵）；新增 `run.side_effect_unknown`；
> `spec.framework` 改自由字符串；新增 `step.effective_tier`（权限档裁决）；
> `x-status-transitions` **不再 required**（唯一权威是 `status-transitions.json`）；
> 顶层加逃生舱（`extensions` + `x-` 前缀字段）。
>
> **v0.4 增量**（相对 v0.3）：
> ① 权威转移表**补边**（`created→waiting_approval`、`paused→failed`、
> `degraded→waiting_approval`、`degraded→outcome_unknown`、`outcome_unknown→cancelled`）
> 与**删边**（`outcome_unknown→running`，安全洞）；
> ② 审计条目新增 `type="state_transition"`，其 payload 必须是
> `{from,to,actor,cause,evidence_ref?}`（`actor` ∈ `control_plane/executor/human/policy`）；
> ③ 新增 **`--replay`** 判据（进 `--all`）：重放状态转移序列，链式连续 + 边合法 +
> 离开 `outcome_unknown` 必须带 `evidence_ref`；
> ④ `audit_entry.type` 由自由字符串改为**枚举**（**只增不删**）。

## 1. 必进字段（**数量自动导出，勿写死**）

> **字段清单的权威来源 = schema**。要当前必填字段与总数，跑：
>
> ```bash
> python tools/contract_check.py --fields --count
> ```
>
> 本文只解释**语义**，不重复列全量字段（v0.1 时代写死的「18 项」已随 v0.3 失效，
> 评审 (18) 要求改成自动导出）。下表是**语义分组**，不是字段清单。

| # | 字段 | 类型 | 必填 | 一句话语义 | 版本 |
|---|---|---|---|---|---|
| 1 | `spec.version` | string（**版本集合** `["0.2.0","0.3.0","0.4.0"]`，不再 `const`） | 是 | 契约版本，与字段演进解耦。新增版本只改 schema 这一处 | v0.3 |
| 2 | `run.status` + 根级 `x-status-transitions` | string 枚举(9) + object | status 是；`x-status-transitions` **否** | run 状态机与合法转移表（转移表是**契约级元数据**，挂在文档根，不塞进每份 run 快照）；`outcome_unknown` = 副作用可能已发生。**转移表可省略**，唯一权威是 `status-transitions.json` | v0.3 |
| 3 | `step[].attempt` / `run.policy.max_attempts` | integer | 是 | 第几次尝试 / 重试上限 | v0.1 |
| 4 | `step[].granted.deadline_at_ms` / `deadline_ms` / `clock` | integer / integer / enum | 是 | 绝对墙钟截止 + 本 step 时长上限 + 时钟约定（`wall`/`monotonic`） | v0.1 |
| 5 | `step[].dedupe_scope` | string \| null | 是（可为 null） | 控制面分配的去重范围（格式 `run_id/step_id/tool`），生命周期绑定 run。**改名自 `idempotency_scope`** | v0.3 |
| 6 | `run.budget.max_steps` / `max_tool_calls` / `max_tokens` / `max_cost_usd` | integer | 是 | 四项配额（金额单位 micro-USD，`reserve → settle` 两段扣费） | v0.1 |
| 7 | `step[].granted.quota` + `run.budget.used_*` | integer | 否（授予时必给） | `sum(used) + granted ≤ quota` 由控制面在同一原子操作内判定；执行面只有额度，不是所有权 | v0.1 |
| 8 | `run.termination.reason` + `run.error` | enum / **object \| null** | 是（`error` 可为 null） | 终止原因 / 错误详情。**v0.3：`error` 可空，去掉 `code:"none"` 哨兵** | v0.3 |
| 9 | `run.audit[]` | array\<object\> | 是（可为空数组） | 审计条目 `{seq, **ts_ms**, type, run_id, step_id, attempt, payload, prev_hash, hash}`，append-only 哈希链。**v0.3：时间戳改 `ts_ms`（epoch 毫秒整数），参与哈希** | v0.3 |
| 10 | `run.tools[]` | array\<object\> | 是（可为空数组） | 工具目录条目 `{tool, tier, args_schema, side_effect, **dedupe_key_template**, result_budget_bytes}`，按档过滤后才下发。**v0.3：`idempotency_key_template` 改名** | v0.3 |
| 11 | `step[].approval.status` | enum(4) | 是 | 审批状态 `pending/approved/rejected/expired` | v0.1 |
| 12 | `step[].args` / `step[].args_ref` | object \| string | 二选一 | 参数本体或大参数引用；`oneOf` 严格二选一（同时给或都不给都非法）。**v0.3：args 允许浮点** | v0.3 |
| 13 | `step[].side_effect` | enum(3) | 是 | 声明式副作用 `none/idempotent/non_idempotent`，控制面据此决定是否允许重试 | v0.1 |
| 14 | `run.idempotency_key` | string | 是 | **业务侧**幂等键（如支付单号），映射 IETF `Idempotency-Key`。**v0.3：删掉 `step.idempotency_key`，幂等 4→2** | v0.3 |
| 15 | `run.policy.retry_profile` | object | 是 | 可执行细化：档位名 `name` + `max_attempts` + `backoff_ms` + `retryable_tools` | v0.1 |
| 16 | `run.budget.currency` + `run.budget.price_snapshot_id` | string(3 位大写) / string | 是 | 成本口径必须指到一份价格快照，否则「成本」不可核 | v0.1 |
| 17 | `run.clock` / `spec.clock` | enum(2) | 是 | 时间注入点（`wall`/`monotonic`），否则 deadline 不可测 | v0.1 |
| 18 | `step[].grant_request` | object | 是 | `request_grant` 入参契约 `{run_id, step_id, attempt, **dedupe_scope**, tool, args/args_ref}`；控制面算 `args_digest` 并随 grant 回传，执行面复算校验 | v0.3 |
| 19 | `run.reservations[]` | array\<object\> | 是（可为空数组） | **v0.3 新增**：预算预留/结算条目 `{id, step_id, attempt, amount_micro, price_snapshot_id, state, settled_micro, refund_micro}`，把 `reserve → settle` 两段扣费**结构化留痕** | v0.3 |
| 20 | `run.side_effect_unknown` | boolean | 是 | **v0.3 新增**：`outcome_unknown` 的**读侧事实**（`status=outcome_unknown` ⇔ `true`，其余状态 ⇔ `false`） | v0.3 |
| 21 | `step[].effective_tier` | enum(3) | 是 | **v0.3 新增**：本 step 的生效权限档，必须等于 `min(policy.capability_tier, tool.tier)`（顺序 `read<write<destructive`） | v0.3 |
| 22 | `run.audit[].type` + `payload.from/to/actor/cause` | enum(12) / string | type 是；四字段是（`type="state_transition"` 时） | **v0.4 新增**：`state_transition` 审计条目承载**状态转移留痕** —— `from`/`to` 是转移两端，`actor` 是发起主体（`control_plane/executor/human/policy`），`cause` 是原因；`evidence_ref` 是可选的外部对账证据（**离开 `outcome_unknown` 时必填**） | v0.4 |

### 1.1 幂等优先级（v0.3 收敛 4→2）

幂等相关字段从 4 个收敛到 **2 个**，并写死优先级：

| 优先级 | 字段 | 语义 |
|---|---|---|
| 1（最高） | `run.idempotency_key` | **业务键**：同键重放 ⇒ 同一 run。映射 IETF `Idempotency-Key`（消费方可放进 HTTP 头） |
| 2 | `step[].dedupe_scope` | **控制面内部去重范围**（格式 `run_id/step_id/tool`）：同 scope 内不重复执行副作用 |
| 3（派生） | `run.tools[].dedupe_key_template` | **模板**：控制面按它派生 scope；本身不是键 |

> v0.1 的 `step.idempotency_key` 与 `step.idempotency_scope` **已删除/改名**：
> 前者与 `run.idempotency_key` 语义重复（业务键只有 run 级一个），后者改名 `dedupe_scope`
> 以免与业务键混淆。

### 1.2 v0.2 字段（不阻塞 M1，不进 required）

| 字段 | 语义 |
|---|---|
| `run.parent_run_id` | 降级/重试链的父 run |
| `run.tenant_quota_ref` | 租户配额引用 |
| 工具目录分页 | 大目录按页下发 |
| `step[].result_digest` | 结果摘要摘要值 |
| `run.labels` | 自由标签 |
| 授权租约 | 见方案 §3.5，M2 落地 |

> 这些字段在 v0.3 **一律不在 schema 里**（`additionalProperties: false` 会拒掉）。
> 引入时按 §4 演进规则 bump minor。
> （例外：顶层 `extensions` 与 `x-` 前缀字段是**逃生舱**，永远允许，见 §4.3。）

### 1.3 枚举全表（v0.3）

| 枚举 | 取值 |
|---|---|
| `run.status` | `created` `running` `waiting_approval` `paused` `degraded` `succeeded` `failed` `cancelled` `outcome_unknown` |
| `run.policy.capability_tier` / `step[].effective_tier` | `read` `write` `destructive` |
| `run.policy.retry_profile.name` | `model_only` `read_only` `full` |
| `step[].side_effect` / `run.tools[].side_effect` | `none` `idempotent` `non_idempotent` |
| `step[].approval.status` | `pending` `approved` `rejected` `expired` |
| `clock`（`spec.clock`/`run.clock`/`granted.clock`） | `wall` `monotonic` |
| `run.tools[].tier` | `read` `write` `destructive` |
| `run.termination.reason` | `completed` `tool_error` `budget_exhausted` `deadline_exceeded` `approval_denied` `cancelled_by_caller` `degraded_circuit_break` |
| `run.error.code` | `tool_failed` `budget_exhausted` `deadline_exceeded` `approval_denied` `policy_violation` `internal_error`（**`none` 已删**） |
| `run.reservations[].state` | `held` `settled` `released` `expired` |
| `run.audit[].type`（**v0.4 起是枚举**） | `run_created` `run_started` `run_finished` `approval_decision` `audit_gap` `side_effect_unknown` `tool_call` `tool_call_denied` `degraded` `step_started` `step_outcome_unknown` `state_transition` |
| `run.audit[].payload.actor`（**v0.4，仅 `state_transition`**） | `control_plane` `executor` `human` `policy` |

> **`spec.framework` 不再是枚举**（v0.3）：它是**自由字符串**。框架清单会变，
> 枚举会把消费方钉死（新增框架即成 breaking change）。只做形状，不做优劣评价。

## 2. 六条语义（每条写明「谁裁决」与「失败语义」）

| # | 语义 | 规则 | **谁裁决** | **失败语义** |
|---|---|---|---|---|
| 1 | **幂等** | run 级 `run.idempotency_key`（业务键）+ step 级 `step[].dedupe_scope`（同 scope 内不重复执行副作用）。重放同 key ⇒ 同一 run，不重复扣费、不重复副作用 | **控制面**（原子存储） | scope 相同但 `args_digest` 不同 ⇒ **拒绝并记审计**，不覆盖；scope 已被执行过 ⇒ `request_grant` 返回 `skip` + 上次结果摘要，工具不调用 |
| 2 | **截止时间** | run 总 deadline 逐 step 下发剩余（`granted.deadline_at_ms` + `granted.deadline_ms`）；执行面用本地单调钟计时但**不自行宣布超时**，只上报「已超授予时长」 | **控制面** | 超时 ⇒ run 落 `failed` + `termination.reason=deadline_exceeded` + `error.code=deadline_exceeded`；执行面自报超时**不被承认** |
| 3 | **分级重试** | 三档 `model_only` / `read_only` / `full`；是否可重试由 `step[].side_effect` + `retry_profile` **共同决定**，非幂等副作用不自动重试；模型调用可重试（无外部副作用） | **控制面** | 超出 `max_attempts` ⇒ 落 `failed`（`error.code=tool_failed`）；非幂等副作用在失败瞬间 ⇒ 见语义 6 的 `outcome_unknown`，**不是** `failed` |
| 4 | **预算与降级** | 四项配额逐 step `reserve → settle`（**结构化留痕见 `run.reservations[]`**）；重试**不二次预扣**但必须 settle 实际用量，差额退回；超限优先级写死 `max_steps → max_tool_calls → max_tokens → max_cost`（先砍数量维度，成本最后砍）；先降级后熔断 | **控制面**（`sum(used) + granted ≤ quota` 同一原子操作） | 降级到底仍超限 ⇒ run 落 `failed` + `termination.reason=budget_exhausted` + `error.code=budget_exhausted`；降级必须留痕（审计链可读出降级前后策略） |
| 5 | **权限分档** | `read` / `write` / `destructive` + 审批档；run 级能力上限写 `run.policy.capability_tier`，工具目录按档过滤后下发（MCP 策略代理）——不能做的工具根本不下发。**冲突裁决**：`effective_tier = min(policy.capability_tier, tool.tier)`（顺序 `read<write<destructive`），每个 step 写 `step[].effective_tier`，校验器逐 step 断言 | **控制面**（策略执行器 PEP） | 越权调用 ⇒ 工具不执行；`step[].approval.status=rejected` ⇒ 落 `failed` + `termination.reason=approval_denied` + `error.code=approval_denied`；审批过期 ⇒ 同上（`status=expired`） |
| 6 | **审计** | 哈希链 append-only（run/step/tool 级），可重放校验。**时间戳用 `ts_ms`（epoch 毫秒整数），进哈希**。**v0.4：状态推进必须留痕**（`state_transition` 条目，见 §2.3）—— 只有留痕了才能重放 | **控制面** | 断链 ⇒ **报**（校验失败即报，不静默修复）；链断 ⇒ 恢复流程拒绝继续，恢复前先查幂等存储。**v0.4：重放不符 ⇒ 报 RFC 6901 路径**（`/run/audit/<i>/payload…`） |

### 2.1 非幂等副作用失败的交代（第七条，语义 3/6 的延伸）

非幂等副作用失败时：

* run 状态落 **`outcome_unknown`（不是 `failed`）**，并带 **`side_effect_unknown = true`**（v0.3 已进 schema）；
* 审计记 attempt / `args_digest` / 工具返回 / 终止原因；
* **向调用方返回「结果未知，请查询副作用状态」而不是「失败」** ——
  因为非幂等副作用失败时，调用方最危险的动作就是直接重试；
* 恢复时**先查幂等存储**，再决定要不要重放。

> 裁决者：**控制面**。执行面只上报，不自行宣布「已失败可重试」。

### 2.2 `reserve → settle` 的四条规则（v0.3）

1. **retry 复用同一 `reservation.id`**：同一 step 的重试**不二次预扣**，复用同一条预留；
2. **`settle` 只由控制面执行**：执行面只能上报实际用量，不能自己写 `state=settled`；
3. **快照在 reservation 生命周期内冻结**：`price_snapshot_id` 结算时不得换（换快照 = 成本口径变了）；
4. **失效须 `release` 并留审计**：`state` 走 `released` / `expired`，且审计链里必须有对应条目。

> 金额一律整数 micro-USD：`amount_micro` / `settled_micro` / `refund_micro` 都是 integer，禁浮点。

### 2.3 状态转移留痕与重放（v0.4 · 语义层的核心）

**要保证的性质**：**篡改任一状态转移 ⇒ 同时破坏哈希链与转移表校验**。

怎么做到的？两步：

1. **留痕**：每一次状态推进都往审计链写一条 `type="state_transition"` 的条目：
   ```json
   { "seq": 1, "ts_ms": 1793613601000, "type": "state_transition",
     "run_id": "run_xxx", "step_id": "step_0", "attempt": 1,
     "payload": { "from": "running", "to": "waiting_approval",
                  "actor": "policy", "cause": "tool requires human approval" },
     "prev_hash": "…", "hash": "…" }
   ```
   * `from` / `to` ∈ 9 个状态；`actor` ∈ `control_plane` / `executor` / `human` / `policy`；
   * `cause` 是人可读的原因（非空）；
   * `evidence_ref` 可选，但**离开 `outcome_unknown` 时必填**（外部对账证据）。

2. **重放**：`--replay` 从 genesis 起重放所有 `state_transition` 条目，五条判据：

   | # | 判据 | 违反时的路径 |
   |---|---|---|
   | 1 | 第一条的 `from` 必须是 `created` | `/run/audit/<i>/payload/from` |
   | 2 | 后一条的 `from` == 前一条的 `to`（链式连续） | `/run/audit/<i>/payload/from` |
   | 3 | 每条 `from→to` 必须在权威表里合法 | `/run/audit/<i>/payload` |
   | 4 | `from == outcome_unknown` ⇒ `evidence_ref` 必填 | `/run/audit/<i>/payload/evidence_ref` |
   | 5 | `from` / `to` 必须是合法状态名 | `/run/audit/<i>/payload/from` |

   ```bash
   python tools/contract_check.py --replay
   ```

**为什么这两步合起来才是完整保证**：

* 改审计里的 `to` ⇒ **哈希链断**（`hash` 重算对不上）**且** replay 判红（边非法或链式断裂）；
* 改权威表里的边 ⇒ **转移表校验断**（fixture 内嵌表与权威表不再相等）**且** replay 判红（边不在权威表内）；
* 绕过黑名单把 `outcome_unknown → running` 加回权威表 ⇒ **读表即判红**（`forbidden` 段命中）。

> **谁裁决**：**控制面**。执行面只上报状态变化，**不自己写** `state_transition` 条目
> （审计链是控制面的 append-only 日志）。

## 3. `args_digest` 规范化算法

算错幂等全错。契约写死（方案 §4.3）：

1. **类型**：`args` 允许 `string` / `number` / `integer` / `boolean` / `null` / `array` / `object`。
   **v0.3 放开浮点**（LLM 参数如 `temperature=0.7` 必须能表达）。
   **金额/预算字段仍禁浮点**（`*_usd` / `*_micro` 一律 integer）。
   浮点按 **ECMAScript Number::toString** 序列化（JCS §3.2.2.3）——
   注意这与 Python 的 `repr(float)` 不同（`1e+16` vs `10000000000000000000`、`1e-07` vs `1e-7`、`1.0` vs `1`）。
2. **对象键按码位序排序**；**数组顺序参与**（数组有序）；**null 值的键剔除**（未传 ≠ 传 null）；**Unicode 归一化取 NFC**。
3. `sha256(canonical_json)`，`canonical_json` = 紧凑 JSON（`,` 与 `:` 后无空格）+ 非 ASCII 原样输出的 UTF-8 字节。
   **Java 与 Python 各写一份实现，用同一组 fixture 逐字节比对。**

> ⚠️ **两套排序口径，别混用**（TASK-035 §2.15 的关键结论）：
>
> | 口径 | 排序 | 用在哪 |
> |---|---|---|
> | **args_digest canonical** | **码位序**（NFC 归一化后比） | 本节；`tools/contract_check.py::canonicalize` / Java `Canonicalizer` |
> | **纪律⑤ JCS** | **UTF-16 码元序**（RFC 8785 §3.2.3） | 审计链哈希；`tools/contract_check.py::jcs` / Java `Jcs` |
>
> 两者**在 ASCII 键名下等价**，非 ASCII 键名下**分叉** ⇒ 两端必须各用各的，别把 `sort_keys` 与
> `String.compareTo` 混进同一个算法。Java 的 `String.compareTo` 天然是 UTF-16 码元序（⇒ JCS 口径），
> `Canonicalizer.compareCodePoints` 才是码位序（⇒ args_digest 口径）。

### 3.1 两个完整样例（与 `--digest` 实跑一致）

**样例 A** —— `contract/fixtures/valid/step_full.json`

输入 `step[0].args`：

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

输入 `step[0].args_ref`（字符串；引用内容不解析）：

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

**样例 C（v0.3 新增）** —— `contract/fixtures/valid/step_float_args.json`

输入 `step[0].args`（含 LLM 浮点参数）：

```json
{
  "prompt": "总结这段契约",
  "temperature": 0.7,
  "top_p": 0.95,
  "max_tokens": 1024,
  "seed": 42
}
```

规范化（浮点按 ES Number::toString；`0.7` → `0.7`、`0.95` → `0.95`；键按码位序）：

```
canonical: {"max_tokens":1024,"prompt":"总结这段契约","seed":42,"temperature":0.7,"top_p":0.95}
sha256: d013cd0ed1f3a20a3762f34baaa380ff5161a66a654a189aa0948a6415ef1104
```

复现命令：

```bash
python tools/contract_check.py --digest contract/fixtures/valid/step_float_args.json
```

## 4. 演进规则（6 条）

1. **契约 ID 是契约，永不重命名**；ID 变了 = 换契约。**版本标在 `spec.version`**；版本变了 = 还是同一契约。
2. 新增字段向后兼容；删除或改语义 = **major**。
3. **JSON Schema 一律 `additionalProperties: false`**（含嵌套每一层）——
   新增字段必须 bump **minor**，否则旧端静默丢弃新字段。
   **例外（逃生舱，v0.3）**：顶层允许 `extensions`（自由 object）与 `x-` 前缀字段；
   `audit[].payload` 允许 `extra`。**逃生舱不参与语义、不参与哈希。**
4. **枚举只能增不能删**（删枚举值 = major）；每个枚举值在 Java/Python 两端必须有实现，
   **CI 自动检查「schema 枚举集合 ⊆ 两端实现集合」**。
   （`spec.framework` v0.3 起**不再是枚举**，故不受此条约束。）
5. **字段重命名 = 新字段 + ≥1 个 minor 的废弃期**（「ID 永不重命名」只覆盖枚举值，不覆盖结构体字段）。
6. **双端实现清单进 CI**：契约变更 ⇒ 双端实现清单必须同步更新，且 conformance 逐字节比对必须仍绿。

### 4.1 `spec.version` 是**版本集合**（v0.3 起）

v0.1 的 `spec.version` 是 `const "0.1.0"` ⇒ 每加一个版本都要改 const（且旧文档无法再通过校验）。
v0.3 起改为**版本集合**（当前 `enum: ["0.2.0", "0.3.0", "0.4.0"]`）：

* **新增版本只改这一处**（往 enum 里加一项）；
* 旧版本文档**仍能通过校验**（老消费方按老语义读）；
* 校验器断言「**版本四处一致**」：`schema.title` / `spec.version` 集合的当前值 /
  `status-transitions.json` 的 `version` / 本文的版本行 —— 任一处漂移即红。

> **v0.4**：集合加了 `"0.4.0"`；`$id` 也带上了版本段
> （`https://qlheric.github.io/agent-run-semantics-contract/v0.4.0/run_semantics.schema.json`）——
> **契约 ID 永不重命名**（仍是同一个契约，只是版本变了）。

## 5. 本契约明确**不做**什么

* 不规定实现语言、不规定框架、不规定存储形态；
* 不规定审批流的具体交互（只定 `approval` 的四态形状）；
* 不规定审计链哈希的具体算法（只定字段形状与 append-only 语义）；
* 不接 LLM，不含任何提示词/模型身份相关内容。

> 再次强调：**本文只定形状、不定实现。**
