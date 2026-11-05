# 双端一致性比对协议（Conformance Protocol v0.1）

> 目的：证明 **Java 侧（TASK-001）与 Python 侧** 对同一份契约的理解**逐字节一致**。
> 唯一真源 = `contract/run_semantics.schema.json`。
> 本文件规定：Java 侧要实现什么、命令长什么样、期望输出是什么、不一致怎么办。
>
> **本 schema 描述的是「run 快照文档」**（`termination` / `error` / `audit` / `tools` 是快照字段），
> **不是 HTTP 请求体**；请求体形状在 TASK-001 另定。

## 1. 协议要点（4 条硬要求）

1. **同 fixtures**：两端跑**同一目录** `contract/fixtures/{valid,invalid}/`，不得各造样例。
2. **同排序**：所有输出键排序、数组保序、金额整数、UTF-8 无转义。
3. **无时间戳 / 无绝对路径**：输出里**不许**出现时间戳、机器路径、随机数、进程 ID。
   → 双端比的是**字符串**，字符串里出现环境相关字符 ⇒ 不可比。
4. **逐字节比对**：用 `diff` 比输出文件，不是「看起来一样」。

## 2. Python 侧参考实现（TASK-000 已交付）

| 命令 | 期望输出（stdout） | exit |
|---|---|---|
| `python tools/contract_check.py --schema` | `schema ok` | 0 |
| `python tools/contract_check.py --all` | 5 行 `rejected <相对路径> path=<RFC6901>` + `valid=N/N passed` + `invalid=5/5 rejected` | 0 |
| `python tools/contract_check.py --coverage` | 单行 JSON（键排序）`{"boundaries":{...},"enums":{...},"missing":[]}` | 0（`missing` 非空 ⇒ 3） |
| `python tools/contract_check.py --fields` | 单行 JSON `{"contract":[...],"run":[...],"spec":[...],"step":[...]}` | 0 |
| `python tools/contract_check.py --digest <fixture.json>` | 两行：`canonical: <json>` / `sha256: <64 位小写 hex>` | 0 |

### 2.1 输出格式细则（Java 侧必须逐字节复现）

* 行尾 **LF**（不是 CRLF）；文件编码 **UTF-8**。
* `--all` 的 `rejected` 行按 **文件名升序**输出（Python 侧按 `sorted(glob)`）。
* `rejected` 行格式固定：`rejected <相对路径> path=<RFC6901 路径>`。
  * 相对路径形如 `contract/fixtures/invalid/float_amount.json`（正斜杠）。
  * RFC 6901 路径形如 `/run/budget/max_cost_usd`；根为空时写 `/`。
* `--coverage` / `--fields` 输出**单行 JSON**，分隔符无空格，键字典序，`ensure_ascii=False`。
* `--digest` 的 `canonical:` 后是**紧凑 JSON**（`,` 与 `:` 后无空格）。

### 2.2 RFC 6901 路径推导规则（两端必须一致）

| 违规类型 | 路径形态 | 例 |
|---|---|---|
| 缺 required 字段 | 父路径 + 缺失字段名 | `/run/policy` |
| 枚举越界 | 字段自身路径 | `/run/status` |
| 类型不符 / 最小值约束 | 字段自身路径 | `/run/budget/max_cost_usd` |
| 多余字段（`additionalProperties:false`） | 父路径 + 多余字段名 | `/run/extra_field` |

> 一条文档可能同时命中多条错误：**只报第一条**，按「路径层级深 → 浅、同一层按字段名字母序」排序。

## 3. Java 侧（TASK-001）要实现什么

### 3.1 必须实现的命令（与 Python 侧一一对应）

建议命令形态：

```bash
mvnw -q exec:java -Dexec.args="--all"
mvnw -q exec:java -Dexec.args="--schema"
mvnw -q exec:java -Dexec.args="--coverage"
mvnw -q exec:java -Dexec.args="--fields"
mvnw -q exec:java -Dexec.args="--digest contract/fixtures/valid/step_full.json"
```

（TASK-001 若选了别的形态（例如独立 `contract-check` 模块或 main class），
**必须在本文更新命令形态**并让两端输出保持一致；本文与实现不一致时以本文为准修实现。）

> **退出码纪律（2026-11-05 补 · 实测）**：`mvnw -q exec:java` 的**进程退出码会被 Maven 折叠**
> （wrapper 在 `ERRORLEVEL ≥ 1` 时统一 `exit 1`）⇒ **`invalid/` 期望的 `exit 2` 拿不到**，而 stdout 仍逐字节一致。
> 需要对拍退出码的命令，用**直连 JVM 形态**（实现侧提供；当前为）：
>
> ```bash
> python java/control-plane/contract_check.py --all
> python java/control-plane/contract_check.py --digest contract/fixtures/invalid/float_amount.json   # 期望 exit 2
> ```
>
> 即：**stdout 用 `mvnw exec:java` 对拍即可；退出码必须用直连形态**；两种形态的输出必须一致。

> **状态转移表**：唯一权威 = `contract/status-transitions.json`（口径见 `README.md`）。
> `schema.json` 只有形状；`fixtures/*.json` 的 `x-status-transitions` 是场景样例，**不得当权威**。

### 3.2 必须实现的四件事

| # | 事项 | 验收方式 |
|---|---|---|
| 1 | **Schema 校验**：用同一份 `run_semantics.schema.json` 校验 `valid/` 全过、`invalid/` 全拒，**拒的路径与 Python 侧一致** | 逐字节 diff `--all` 输出 |
| 2 | **canonical + digest**：Java 侧独立实现 §算法（`sort_keys` 等价语义、数组保序、null 键剔除、NFC 归一化、UTF-8 无转义），输出两行 | 逐字节 diff `--digest` 输出 |
| 3 | **枚举实现覆盖**：Java 侧枚举常量集合 **⊇ schema 枚举集合**；少一个就红 | CI 检查（见 §5） |
| 4 | **浮点防线**：Java 侧遇到 `*_usd` 非整数 / args 含浮点 ⇒ 拒绝（不是静默取整） | `invalid/float_amount.json` 被拒 |

### 3.3 Java 侧 canonical 实现要点（最容易写错的地方）

* **对象键排序**：Java 的 `Map` 不保证顺序 ⇒ 必须显式按**码位序**（`String.compareTo`，UTF-16 码元序）排序后输出。
  ⚠️ 本契约的 fixture 键名均为 ASCII，UTF-16 序与码位序一致；若将来引入非 BMP 字符，两端需统一按**码位**（code point）序。
* **数组保序**：用 `List`，不要用 `Set` / `HashMap`。
* **null 键剔除**：键存在但值为 `null` ⇒ 整个键从输出中删除（不是输出 `"k":null`）。
* **NFC 归一化**：Java 用 `java.text.Normalizer.normalize(s, Normalizer.Form.NFC)`；
  Python 用 `unicodedata.normalize("NFC", s)`。
* **不转义非 ASCII**：输出 UTF-8 原字节，**禁止** `\uXXXX` 转义（Python 侧 `ensure_ascii=False`）。
* **整数不带小数点**：micro-USD 整数输出 `100000` 而不是 `100000.0`。
* **浮点直接失败**：解析到浮点金额或浮点 args ⇒ 报错退出（Python 侧 exit 2），**不许**转义或截断。

## 4. fixtures 清单与用途

### 4.1 `contract/fixtures/valid/`（9 条，全部必须通过）

| 文件 | 用途 | 覆盖要点 |
|---|---|---|
| `step_full.json` | **基准样例**（双端 digest 比对的主样例） | 四项预算全填满；args 含中文、null 值、乱序嵌套对象；`framework=langgraph`；`retry_profile=full`；`side_effect=none`；`approval=approved`；`clock=wall` |
| `step_args_ref.json` | 大参数引用边界 | **无 `step.args`**，只有 `args_ref`；`idempotency_scope=null`；`framework=custom`；`policy.capability_tier=read`；`retry_profile=model_only`；`clock=monotonic`；`approval=pending` |
| `run_created.json` | `created` 状态 | `framework=agent_framework`；`policy.capability_tier=write`；`retry_profile=read_only`；`step.side_effect=idempotent`；`approval=rejected` |
| `run_waiting_approval.json` | `waiting_approval` 状态 | `approval.required=true` + `status=pending`；`retry_profile=model_only` |
| `run_degraded.json` | `degraded` 状态 | `step.side_effect=non_idempotent`（不自动重试）；预算已用满 |
| `run_succeeded.json` | `succeeded` 状态 | `termination.reason=completed` + `error.code=none` |
| `run_failed.json` | `failed` 状态 | `termination.reason=tool_error` + `error.code=tool_failed`；`step.attempt=3`（边界） |
| `run_cancelled.json` | `cancelled` 状态 | `termination.reason=cancelled_by_caller`；`approval.status=expired`（边界） |
| `run_outcome_unknown.json` | `outcome_unknown` 状态 | 非幂等副作用失败的交代；审计里带 `args_digest` |

> 覆盖判定由 `python tools/contract_check.py --coverage` 自动做：
> 9 值 `run.status` 全出现、3 值 `framework`、3 值 `run.policy.capability_tier`（`read`/`write`/`destructive`）、
> 3 值 `retry_profile`、3 值 `side_effect`、4 值 `approval.status`、2 值 `clock`，
> 以及 6 个边界（`scope_is_null` / `attempt_ge_2` / `args_ref_used` / `outcome_unknown` /
> `approval_expired` / `all_budgets_set`）。

### 4.2 `contract/fixtures/invalid/`（恰好 5 条，全部必须被拒）

| 文件 | 触发点 | **期望拒绝路径** | 判据 |
|---|---|---|---|
| `missing_required.json` | 缺 required 字段（`run.policy`） | `/run/policy` | ① |
| `enum_out_of_range.json` | 枚举越界（`run.status = "running_v2"`） | `/run/status` | ② |
| `float_amount.json` | 金额用浮点（`max_cost_usd = 1.5`） | `/run/budget/max_cost_usd` | ③ |
| `extra_property.json` | 多余字段（`run.extra_field`） | `/run/extra_field` | ④ |
| `negative_budget.json` | 负数预算（`max_cost_usd = -1`） | `/run/budget/max_cost_usd` | ⑤ |

> 这 5 条拒绝路径是**跨语言 conformance 的核心断言**：
> 两端必须报**同一条路径**，不是「都拒了」就算过。

## 5. 「不一致怎么办」

**顺序固定，不许跳步**：

1. **先查 fixture 覆盖**：跑 `python tools/contract_check.py --coverage`。
   若 `missing` 非空 ⇒ 是**覆盖缺口**，先补 fixture（补在 `valid/` 或 `invalid/`，并同步更新本文 §4 表格）。
2. **再查 canonical 实现**：若两侧拒绝路径不同 ⇒ 是**路径推导规则**不一致（见 §2.2），
   以 **schema + 本文 §2.2** 为准，改实现，**不许**改 fixture 迁就某一侧。
3. **再查枚举实现集合**：若某端缺枚举实现 ⇒ 是 §3.2 第 3 条的红灯，先补实现。

**铁律**：

* **不许两边各改一处糊过去** —— 两边各改一处 = 契约漂移 = 本协议失效。
* **不许改 schema 来迎合某一端的 bug** —— schema 是唯一真源；有分歧时改实现。
* **不许改 fixture 的期望拒绝路径来"对齐"** —— 期望路径由本文 §4.2 表格定义，
  改它 = 改判据，必须由大脑拍板。
* 发现分歧 ⇒ **写进回执「需要拍板」**，附：两端完整输出 + 差异行号。

### 5.1 CI 检查清单（双端流水线）

| # | 检查 | 判据 |
|---|---|---|
| 1 | 两端 `--all` 输出逐字节一致 | `diff` 空 |
| 2 | 两端 `--digest` 输出逐字节一致（全部 9 条 valid fixture 各跑一遍） | `diff` 空 |
| 3 | 两端 `--coverage` 输出逐字节一致，且 `missing` 为空 | `diff` 空 + `missing=[]` |
| 4 | 两端 `--fields` 输出逐字节一致 | `diff` 空 |
| 5 | schema 枚举集合 ⊆ 两端枚举实现集合 | 缺一个即红 |
| 6 | Python 侧 `--schema` 自检（含三条纪律）绿 | exit 0 |

## 6. 已知边界（v0.1 明确不支持）

* **canonical 只支持这些类型**：`string` / `integer` / `boolean` / `null` / `array` / `object`。
  浮点一律拒绝（exit 2），不做任何浮点格式化。
* **`args_ref` 只做字符串 digest**：只对**引用字符串本身**求 digest，**不解析引用指向的内容**。
  ⇒ 引用内容变了但引用串没变，digest 不变；这是 v0.1 的已知取舍（M2 再议）。
* **canonical 不做语义等价**：两段 JSON 若字节不同但语义相同（除本算法规定的规范化外），
  digest 仍可能不同 —— 算法是**规范化**，不是**等价归并**。
* **Unicode 排序口径**：v0.1 fixtures 的键名均为 ASCII，UTF-16 码元序与码位序一致；
  引入非 BMP 键名前，两端必须统一为**码位序**（Python 已按码位序实现，Java 需另行处理）。
* **`args_schema` 不做完整 JSON Schema 求值**：v0.1 只定粗粒度形状（`type` / `required` / `enum`），不做参数级校验。
* **`spec.framework` 不做优劣评价**：只是形状字段，不表示推荐哪个框架。

## 7. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | 2026-11-02 | 首版（随 TASK-000 落盘）：协议、fixtures 清单、拒绝路径、CI 清单、已知边界 |
| v0.1 (TASK-000b) | 2026-11-02 | `capability_tier` 从 `spec` 归位到 `run.policy`（三值 `read`/`write`/`destructive`）；`x-status-transitions` 从 `run` 移到文档根；`--fields` 改为 4 键（新增 `contract`）。**5 条 invalid 的期望拒绝路径不变**，`--digest` 结果不变 |
| v0.1 (T2a 补) | 2026-11-05 | §3.1 补「**退出码纪律**」：`mvnw exec:java` 会把 ≥1 的退出码折叠成 1 ⇒ invalid 的 `exit 2` 拿不到；退出码敏感对拍改用直连 JVM 形态 `python java/control-plane/contract_check.py …`。**协议、fixtures、5 条期望路径与 `--digest` 结果均未改** |
