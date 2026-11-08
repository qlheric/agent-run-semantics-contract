# 双端一致性比对协议（Conformance Protocol v0.5.0）

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
| `python tools/contract_check.py --all` | 9 行 `rejected <相对路径> path=<RFC6901>` + `valid=N/N passed` + `invalid=9/9 rejected` | 0（fixture 不符预期 ⇒ 2） |
| `python tools/contract_check.py --verify-chain` | 每条含 audit 的 valid fixture 一行 `chain ok <相对路径> entries=<N>` + 汇总行 `chain=<通过>/<总数> verified` | 0（有链不符 ⇒ 2） |
| `python tools/contract_check.py --replay` | 每条含 audit 的 valid fixture 一行 `replay ok <相对路径> transitions=<N>` + 汇总行 `replay=<通过>/<总数> verified` | 0（有重放不符 ⇒ 2） |
| `python tools/contract_check.py --coverage` | 单行 JSON（键排序）`{"boundaries":{...},"enums":{...},"missing":[]}` | 0（`missing` 非空 ⇒ 3） |
| `python tools/contract_check.py --fields` | 单行 JSON `{"contract":[...],"run":[...],"spec":[...],"step":[...]}` | 0 |
| `python tools/contract_check.py --fields --count` | 上面的单行 JSON **+ 一行** `count=<N>`（必填字段总数，自动导出；README 引用它，不写死数字） | 0 |
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

#### 2.2.1 ⚠️ 顶层 `patternProperties` + `additionalProperties:false` 的**多余字段路径 = 父路径**（v0.4 写死）

本契约顶层同时有：

```json
"additionalProperties": false,
"patternProperties": { "^x-[A-Za-z0-9_.-]+$": { "$ref": "#/$defs/json_value" } }
```

当文档出现一个**既不在 `properties` 里、也不匹配任何 `pattern`** 的顶层字段时，
jsonschema 报的是 **`additionalProperties` 校验器**，消息形态是：

```
'<字段名>' does not match any of the regexes: '^x-[A-Za-z0-9_.-]+$'
```

⚠️ **这条消息里 Python 侧的 `pointer_of()` 抓不到字段名**（它只在
`'xxx' was unexpected` 与 `'xxx' is a required property` 两种措辞里补字段名）
⇒ **路径停在父路径**（顶层就是 `/`），**不是** `/<字段名>`。

**Java 侧必须逐字节复刻这条口径**（`SchemaValidator.validateObject()` 的
`patternMismatchMessage()` 分支）：否则两端 `--all` 输出会分叉。

> **为什么现在写死**：TASK-038 曾在这里分叉过一次（Java 报 `/<字段名>`、
> Python 报 `/`），靠「顶层 `patternProperties` 命中时不额外报 additionalProperties」
> 绕过去了 —— 但那条绕法对**顶层**成立、对**嵌套层**不成立。
> v0.4 起把口径钉在协议里：**凡是「带 `patternProperties` 的 object」，
> 其多余字段一律报父路径**（消息形态也照抄 jsonschema）。
>
> 补充：多个多余字段时 jsonschema 只报**一条**（字段名用 `, ` 连接，
> 动词用 `do not`）；单个时用 `does not`。

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
> `schema.json` 只有形状；`fixtures/*.json` 的 `x-status-transitions` 是**场景样例**，
> **不得当权威**；但自 TASK-034 起受**纪律④**约束：**必须与权威表逐状态、逐目标（排序后）相等**
> （见 §3.5）⇒ 样例表无法与权威漂移。
>
> **v0.3 起 `x-status-transitions` 不再是 required**（唯一权威是 `status-transitions.json`）——
> 文档**可以不带**这张表；带了的话**必须与权威表相等**（纪律④照旧生效）。

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

## 3.5 两条新增纪律（TASK-034）

> 本节是**协议的一部分**：Java 侧与 Python 侧都必须实现，否则两端 `--all` 输出会分叉。

### 纪律④：fixture 内嵌转移表必须与权威表**逐状态、逐目标（排序后）相等**

* **唯一权威** = `contract/status-transitions.json`（`states` + `transitions`，空数组 = 终态）。
* `contract/run_semantics.schema.json` 只定**形状**（`$defs/status_targets`），**不承载边**。
* `contract/fixtures/valid/*.json` 根级的 `x-status-transitions` **必须与权威表逐状态、逐目标完全相等**
  （目标按字母序比较 ⇒ 数组顺序不敏感、集合必须相同）。
* 校验器**必须真读** `contract/status-transitions.json`；**读不到 ⇒ 直接 exit 1，不许静默跳过**
  （否则纪律④形同虚设：校验器假装检查，实际没查）。
* 不相等 ⇒ 失败，消息形如：
  `state: fixture=[...] authority=[...]`，路径 `/x-status-transitions/<state>`。

> **为什么保留 fixture 内嵌这张表**：它让每份 fixture **自证**（单独拿走一份也能知道边），
> 但**不许当权威**；纪律④保证「样例表」无法与权威漂移。
> （历史背景：TASK-004 时 9 份 fixture 的内嵌表互相矛盾，本纪律是为了终结那种状态。）

### 纪律⑤：审计链必须满足固定公式，`--verify-chain` 为**必跑项**

**哈希公式（写死，两端一致；v0.3：时间戳改 `ts_ms` 整数）**：

```
canonical = JCS([ str(seq), str(ts_ms), type, run_id, step_id, str(attempt), JCS(payload), prev_hash ])
hash      = sha256( canonical.encode("utf-8") ).hexdigest()
```

> **v0.3 的唯一变化**：`ts`（ISO 字符串）→ `ts_ms`（epoch 毫秒**整数**），公式里用 `str(ts_ms)`。
> 好处：时间戳**参与哈希**且没有格式歧义（ISO 字符串有无穷多种等价写法，毫秒整数只有一种）。
> ⚠️ 两端必须**同步**改公式 —— 旧公式（带 `ts` 字符串）的哈希值全部作废。

**`JCS(x)`（RFC 8785）的本实现口径**：

| 项 | 口径 |
|---|---|
| 键排序 | **UTF-16 码元序**（RFC 8785 §3.2.3 原文口径）。**Java 的 `String.compareTo` 天然就是这个口径**；Python 侧**必须显式实现**（`key=lambda s: s.encode("utf-16-be")` 一类），**不能**用 `sort_keys=True`（那是**码位序**）。⇒ 两端天然一致，不再依赖「键名都是 ASCII」这个前提 |
| 空白 | 无（`separators=(",", ":")`） |
| 非 ASCII | 不转义（`ensure_ascii=False`），UTF-8 原样输出 |
| 数组 | **保序** |
| 数字 | 按 **ECMAScript Number::toString**（JCS §3.2.2.3）。⚠️ 与 Python `repr(float)` 不同：`1e+16` → `10000000000000000000`、`1e-07` → `1e-7`、`1.0` → `1` |

> ⚠️ **两套排序口径，别混用**（TASK-035 §2.15 的关键结论）：
>
> | 口径 | 排序 | 用在哪 |
> |---|---|---|
> | **纪律⑤ JCS** | **UTF-16 码元序**（RFC 8785 §3.2.3） | 本节审计链哈希；Java `Jcs` / Python `jcs()` |
> | **args_digest canonical** | **码位序**（NFC 归一化后比） | README §3；Java `Canonicalizer.compareCodePoints` / Python `canonicalize()` |
>
> 两者在 ASCII 键名下**等价**，非 ASCII 键名下**分叉** ⇒ 两端各用各的。
> Java 的 `String.compareTo` 是 UTF-16 码元序（⇒ JCS 口径），
> `Canonicalizer.compareCodePoints` 才是码位序（⇒ args_digest 口径）—— **两个排序口径不能混用**。

**链式规则**（三条，缺一不可）：

1. **genesis**：第 1 条的 `prev_hash` = 64 个 `0`；
2. **链式**：`entry[i].prev_hash == entry[i-1].hash`；
3. **重算**：`entry[i].hash` == 按上式重算的值；不符 ⇒ 拒绝路径 `/run/audit/<i>/hash`。

**形状约束**：`run.audit[].hash` 与 `run.audit[].prev_hash` 均为
`{"type":"string","pattern":"^[0-9a-f]{64}$"}`（长度与大小写钉死）；
`run.audit[].ts_ms` 为 `{"type":"integer","minimum":0}`（epoch 毫秒）。

**必跑项**：

| 命令 | 覆盖范围 |
|---|---|
| `python tools/contract_check.py --all` | **必须包含**链校验与纪律④检查（不许只在 `--verify-chain` 里才有） |
| `python tools/contract_check.py --verify-chain` | 对所有含 `run.audit` 的 valid fixture 单独复算（输出逐条 `chain ok` + 汇总） |

**退出码**：`--all` fixture 不符预期 ⇒ **exit 2**；权威表 / schema 文件缺失或坏 ⇒ **exit 1**。

## 3.6 两条 v0.3 新纪律（TASK-035）

> 与纪律④⑤同性质：Java 侧与 Python 侧都必须实现，否则两端 `--all` 输出会分叉。

### 纪律⑥：权限档冲突裁决 `effective_tier = min(policy.capability_tier, tool.tier)`

* 顺序写死：`read < write < destructive`（数值小 = 权限小）。
* 每个 step 必须写 `step[].effective_tier`，且它**必须等于**
  `min(run.policy.capability_tier, 工具档)`（工具档 = `run.tools[]` 里 `grant_request.tool` 对应条目的 `tier`）。
* **越权**（policy=read 而 tool=destructive 却写 effective=destructive）⇒ 拒绝路径 `/step/<i>/effective_tier`。
* 消息形如：`effective_tier 越权或写错：期望 min(policy=read, tool=destructive)=read，实际 destructive`。
* 负例：`contract/fixtures/invalid/tier_privilege_escalation.json`（期望路径 `/step/0/effective_tier`）。

### 纪律⑦：`run.side_effect_unknown` 与 `run.status` 一致

* `run.status = outcome_unknown` ⇔ `run.side_effect_unknown = true`；其余状态 ⇔ `false`。
* 不一致 ⇒ 拒绝路径 `/run/side_effect_unknown`。
* 这是 `outcome_unknown` 的**读侧事实**（v0.3 起进 schema，不再只活在 README 散文里）。

## 3.7 v0.4 新纪律⑧：状态转移留痕与 `--replay` 重放（TASK-040）

> 与纪律④⑤⑥⑦同性质：**Java 侧与 Python 侧都必须实现**，否则两端 `--all` / `--replay` 输出会分叉。

### 3.7.1 权威转移表 v0.4.0 的边变更

| 变更 | 边 | 理由 |
|---|---|---|
| **新增** | `created → waiting_approval` | policy 要求先批后跑，避免「先谎报一次 running」 |
| **新增** | `paused → failed` | 系统超时是「结束」不是「被取消」（取消用 `cancelled`） |
| **新增** | `degraded → waiting_approval` | 降级后仍可能需要人批 |
| **新增** | `degraded → outcome_unknown` | 降级执行中失联 ⇒ 副作用状态未知 |
| **新增** | `outcome_unknown → cancelled` | 放弃的 run 可正常收尾 |
| **删除** | `outcome_unknown → running` | ⚠️ **安全洞**：未经对账不得重入执行态（可能重复副作用） |

`outcome_unknown → succeeded / failed` **保留但附 guard**：必须带 `evidence_ref`（见 §3.7.3）。

> **黑名单**：`status-transitions.json` 的 `forbidden` 段显式列出
> `outcome_unknown → running` ⇒ 校验器读到它 ⇒ **直接判红**（不是「悄悄生效」）。
> 这是「安全洞不许复现」的机器可验证保证。

### 3.7.2 审计 `state_transition` 条目

* `audit_entry.type` 枚举新增 `"state_transition"`（其余既有类型保留；**枚举只增不删**）。
* `type == "state_transition"` 时，`payload` **必须**是 `$defs/state_transition_payload`：
  `{from, to, actor, cause, evidence_ref?}` —— 四字段必填，`evidence_ref` 可选。
  * `from` / `to` ∈ `run.status` 的 9 个状态；
  * `actor` ∈ `control_plane` / `executor` / `human` / `policy`；
  * `cause` 是非空字符串（人可读的原因）。
* schema 侧用 `if/then`（`allOf`）约束：见 `$defs/audit_entry.allOf`。

### 3.7.3 `--replay` 判据（**新命令，进 `--all`**）

从 genesis 起按 `seq` 重放所有 `type=="state_transition"` 的条目，**五条判据**：

| # | 判据 | 违反时的路径 |
|---|---|---|
| 1 | 第一条的 `from` 必须是 `created`（run 的初始状态） | `/run/audit/<i>/payload/from` |
| 2 | 后一条的 `from` 必须等于前一条的 `to`（**链式连续性**） | `/run/audit/<i>/payload/from` |
| 3 | 每条 `from→to` 必须在**权威表**里合法 | `/run/audit/<i>/payload` |
| 4 | `from == outcome_unknown` 时 `payload.evidence_ref` **必填** | `/run/audit/<i>/payload/evidence_ref` |
| 5 | `from` / `to` 必须是合法状态名（枚举外 ⇒ 非法边） | `/run/audit/<i>/payload/from` |

任一不满足 ⇒ **exit 2** 并给 RFC 6901 路径。

**命令**：

```bash
python tools/contract_check.py --replay
```

输出（逐字节稳定）：

```
replay ok contract/fixtures/valid/step_replay.json transitions=4
failed <相对路径> path=<RFC6901> (<原因>)
replay=<通过数>/<总数> verified
```

**必跑项**：

| 命令 | 覆盖范围 |
|---|---|
| `python tools/contract_check.py --all` | **必须包含** replay 检查（不许只在 `--replay` 里才有） |
| `python tools/contract_check.py --replay` | 对所有含 `run.audit` 的 valid fixture 单独重放 |

> **检查顺序**（两端必须一致，错了输出会分叉）：
> `schema → 审计链（纪律⑤）→ 转移表一致性（纪律④）→ 权限档裁决（纪律⑥）→ 副作用标志（纪律⑦）→ replay（纪律⑧）`。

## 3.8 v0.5 新纪律⑨：工具注解折叠 MCP ToolAnnotations（TASK-050）

> 与纪律④⑤⑥⑦⑧同性质：**Java 侧与 Python 侧都必须实现**，否则两端 `--all` 输出会分叉。

### 3.8.1 为什么要注解（`annotations`）

v0.4 之前，工具的能力档（`tools[].tier`）与副作用声明（`tools[].side_effect`）是本契约
**自造**的字段 —— 消费方无从判断它们与 MCP 生态里那套
`readOnlyHint / destructiveHint / idempotentHint / openWorldHint` 的对应关系。

v0.5 起 `$defs/tool_entry` 增加 `annotations`（**四键必填，不许省略**）：

```json
"annotations": {
  "readOnlyHint": false,
  "destructiveHint": true,
  "idempotentHint": false,
  "openWorldHint": true
}
```

* **出处**：MCP `ToolAnnotations`（spec 2025-03-26+），schema 里以 `x-source` 标注；
* **四键必填的理由**：省略即「不知道」—— 而消费方**必须**能区分
  「确定没有副作用」与「不知道有没有副作用」。MCP 侧允许缺省（缺省 `destructiveHint=true`），
  本契约**收窄**为全必填（宁可显式写「是破坏性的」，也不要留空让人猜）。

### 3.8.2 映射规则（**写死**，两端一致）

| 推导项 | 规则 |
|---|---|
| `tier` | `destructive if destructiveHint else (read if readOnlyHint else write)` |
| `side_effect` | `none if readOnlyHint else (idempotent if idempotentHint else non_idempotent)` |

推论（等价形式，便于人读）：

* `readOnlyHint=true` ⇒ `tier=read` **且** `side_effect=none`；
* `destructiveHint=true` ⇒ `tier=destructive`；
* `readOnlyHint=false` 且 `idempotentHint=true` ⇒ `side_effect=idempotent`；
* `readOnlyHint=false` 且 `idempotentHint=false` ⇒ `side_effect=non_idempotent`。

> **`openWorldHint` 不在推导链里**：它是 `outcome_unknown` 的**预测变量**
> （与开放世界交互的工具，非幂等调用失败时更可能落 `outcome_unknown`），
> **不是**权限/副作用的函数 ⇒ 自由取值，不参与一致性判定。

### 3.8.3 判据（违反 ⇒ 拒绝路径 `/run/tools/<i>/annotations`）

`annotations` 与 `tier` / `side_effect` 不一致 ⇒ **判红**。

* 路径指向 **`/run/tools/<i>/annotations`**（**不是** `/run/tools/<i>/tier`）：
  判定的事实来源是**注解**，`tier`/`side_effect` 只是被推导出来的那一端。
* 形状不对（缺 hint、hint 不是布尔）⇒ **不重复报错**，交给 schema 报错
  （否则同一处问题报两遍、路径不一致 ⇒ 双端输出分叉）。

**负例**：`contract/fixtures/invalid/invalid_tool_annotations_mismatch.json`
（期望路径 `/run/tools/0/annotations`）——
它的 schema 形状**完全合法**（annotations 四键齐全、值都是布尔），
**只能**靠纪律⑨被拒 ⇒ 它是「纪律⑨真的在跑」的证据。

### 3.8.4 检查顺序（两端必须一致，错了输出会分叉）

`schema → 审计链（纪律⑤）→ 转移表一致性（纪律④）→ 权限档裁决（纪律⑥）→
副作用标志（纪律⑦）→ replay（纪律⑧）→ 工具注解一致性（纪律⑨）`。

## 3.9 v0.5 新口径：`result_digest`（替代 `result`）

### 3.9.1 为什么改

v0.4 的 `audit_payload.result` 是**自由字符串**，而它**参与审计哈希**
（纪律⑤ 的 JCS 数组里含整个 payload）⇒ 同一份工具结果换个措辞、多个字、标点不同，
哈希就变 ⇒ 「同一件事」的哈希对不上、跨实现复算困难。

v0.5 起：`result`（自由字符串）**删除**，改 `result_digest`：

```json
"result_digest": { "type": "string", "pattern": "^[0-9a-f]{64}$" }
```

* **哈希公式不变**（纪律⑤ 的 JCS 数组里 payload 已是 canonical）——
  只是 payload 的**内容**从自由文本变成了固定宽度的摘要；
* 原文（人可读的 detail）走 `extra` 逃生舱或 run 的其它字段，**不参与哈希**；
* 格式非法（不是 64 位小写 hex）⇒ schema `pattern` 拒 ⇒ 路径
  `/run/audit/<i>/payload/result_digest`。

### 3.9.2 摘要口径（**写死**，两端一致）

```
result_digest = sha256( canonical_json(canonicalize(result_value)).encode("utf-8") ).hexdigest()
```

`canonicalize` / `canonical_json` 与 **`args_digest` 同一口径**
（码位序 + NFC + null 键剔除 + ECMAScript Number::toString）⇒ 复用现成实现，
**不另写一套**（两套算法迟早分叉）。

**负例**：`contract/fixtures/invalid/invalid_result_digest_format.json`
（期望路径 `/run/audit/0/payload/result_digest`）。

### 3.9.3 `--verify-chain` / `--replay` 逻辑不变

纪律⑤ 与纪律⑧ 的**判据与公式都没变**（变的只是 payload 里那个字段的值形态）⇒
`--verify-chain` / `--replay` 的实现与输出格式**不动**。
但注意：**payload 变了 ⇒ 哈希变了** ⇒ 存量 fixture 的 `hash` 必须**按新公式重算**
（不是手改，是工具算）。

## 4. fixtures 清单与用途

### 4.1 `contract/fixtures/valid/`（14 条，全部必须通过）

| 文件 | 用途 | 覆盖要点 |
|---|---|---|
| `step_full.json` | **基准样例**（双端 digest 比对的主样例） | 四项预算全填满；args 含中文、null 值、乱序嵌套对象；`framework=langgraph`；`retry_profile=full`；`side_effect=none`；`approval=approved`；`clock=wall`；含 audit 链（2 条） |
| `step_args_ref.json` | 大参数引用边界 | **无 `step[].args`**，只有 `args_ref`；`dedupe_scope=null`；`framework=custom`；`policy.capability_tier=read`；`retry_profile=model_only`；`clock=monotonic`；`approval=pending` |
| `step_float_args.json` | **v0.3 浮点边界** | args 里带 LLM 浮点参数（`temperature=0.7`、`top_p=0.95`）—— v0.3 放开浮点，否则表达不了真实 LLM 参数 |
| `run_multi_step.json` | **v0.3 多步边界** | `step` 是**数组**（2 个 step）；`run.reservations[]` 有 `settled` 与 `released` 两条；`effective_tier` 被工具档压低（policy=write、tool=destructive ⇒ effective=write）；五类 token 计数器齐全 |
| `audit_nonbmp_key.json` | **v0.3 键序边界**（纪律⑤） | payload.extra 里放一对**会分叉**的键名：非 BMP 的 U+1D54A（`𝕊`，UTF-16 = 代理对 D835 DD4A）与 BMP 高位的 U+E000。**按码元序 `𝕊` 在前，按码位序 U+E000 在前** ⇒ 两种实现算出的 hash 不同。本 fixture 的 hash 即「Python 侧已按码元序实现」的证据 |
| `run_created.json` | `created` 状态 | `framework=agent_framework`；`policy.capability_tier=write`；`retry_profile=read_only`；`step.side_effect=idempotent`；`approval=rejected` |
| `run_waiting_approval.json` | `waiting_approval` 状态 | `approval.required=true` + `status=pending`；`retry_profile=model_only` |
| `run_degraded.json` | `degraded` 状态 | `step.side_effect=non_idempotent`（不自动重试）；预算已用满 |
| `run_succeeded.json` | `succeeded` 状态 | `termination.reason=completed` + `error=null`（v0.3 去掉 `code:none` 哨兵） |
| `run_failed.json` | `failed` 状态 | `termination.reason=tool_error` + `error.code=tool_failed`；`step.attempt=3`（边界） |
| `run_cancelled.json` | `cancelled` 状态 | `termination.reason=cancelled_by_caller`；`approval.status=expired`（边界） |
| `run_outcome_unknown.json` | `outcome_unknown` 状态 | 非幂等副作用失败的交代；`side_effect_unknown=true`；审计里带 `args_digest`（含 audit 链） |
| `step_replay.json` | **v0.4 replay 样例**（纪律⑧） | 完整状态转移序列 `created→running→waiting_approval→running→succeeded`；审计里 4 条 `type=state_transition` 条目，链式连续、每条边都在权威表内 |
| `run_unknown_resolved.json` | **v0.4 guard 样例**（纪律⑧） | `outcome_unknown→succeeded` **带 `evidence_ref`**（外部对账证据）—— 证明「带证据可以离开未知态」 |
| `run_outcome_unknown.json` | **v0.5 `result_digest` 样例** | 审计载荷里带 `result_digest`（64 位小写 hex，工具算的真值）—— 证明新字段能表达且能过 `pattern`；**该 fixture 的哈希链已按新载荷重算** |

> **v0.5**：全部 14 份 valid fixture 的 `tools[]` 都带 `annotations`
> （MCP ToolAnnotations 四键）⇒ 纪律⑨在 valid 侧**逐条被验证**（不是只有负例在跑）。

> 覆盖判定由 `python tools/contract_check.py --coverage` 自动做：
> 9 值 `run.status` 全出现、3 值 `run.policy.capability_tier`、3 值 `step[].effective_tier`、
> 3 值 `retry_profile`、3 值 `side_effect`、4 值 `approval.status`、2 值 `clock`，
> 以及 10 个边界（`scope_is_null` / `attempt_ge_2` / `args_ref_used` / `outcome_unknown` /
> `approval_expired` / `all_budgets_set` / `multi_step` / `float_in_args` /
> `reservation_settled` / `tier_downgraded`）。
>
> ⚠️ `spec.framework` v0.3 起**不是枚举** ⇒ 不再要求「覆盖某个框架集合」；
> 但 `--coverage` 仍**统计**出现过哪些值（`enums.framework`），供人看覆盖面。

### 4.2 `contract/fixtures/invalid/`（恰好 14 条，全部必须被拒）

| 文件 | 触发点 | **期望拒绝路径** | 判据 |
|---|---|---|---|
| `missing_required.json` | 缺 required 字段（`run.policy`） | `/run/policy` | ① |
| `enum_out_of_range.json` | 枚举越界（`run.status = "running_v2"`） | `/run/status` | ② |
| `float_amount.json` | 金额用浮点（`max_cost_usd = 1.5`） | `/run/budget/max_cost_usd` | ③ |
| `extra_property.json` | 多余字段（`run.extra_field`） | `/run/extra_field` | ④ |
| `negative_budget.json` | 负数预算（`max_cost_usd = -1`） | `/run/budget/max_cost_usd` | ⑤ |
| `invalid_audit_chain.json` | **审计链第 2 条 hash 被篡改**（schema 合法，仅链断） | `/run/audit/1/hash` | ⑨（纪律⑤） |
| `step_not_array.json` | **v0.3：`step` 是单对象**（应是数组） | `/step` | v0.3 |
| `reservation_bad_state.json` | **v0.3：`reservation.state` 非法**（`pending`） | `/run/reservations/0/state` | v0.3 |
| `tier_privilege_escalation.json` | **v0.3：权限档越权**（policy=read、tool=destructive，却写 effective=destructive） | `/step/0/effective_tier` | 纪律⑥ |
| `invalid_illegal_edge.json` | **v0.4：非法边** `succeeded→running`（终态无出口） | `/run/audit/2/payload` | 纪律⑧（判据 3） |
| `invalid_unknown_no_evidence.json` | **v0.4：`outcome_unknown→succeeded` 缺 `evidence_ref`** | `/run/audit/2/payload/evidence_ref` | 纪律⑧（判据 4） |
| `invalid_replay_gap.json` | **v0.4：链式断裂**（第 2 条 `from=degraded`，上一条 `to=running`） | `/run/audit/1/payload/from` | 纪律⑧（判据 2） |
| `invalid_tool_annotations_mismatch.json` | **v0.5：工具注解与 tier/side_effect 不一致**（annotations 四键齐全、值合法，但 `destructiveHint=true` 与 `tier=read` 冲突） | `/run/tools/0/annotations` | 纪律⑨（§3.8） |
| `invalid_result_digest_format.json` | **v0.5：`result_digest` 不是 64 位小写 hex**（长度不足） | `/run/audit/0/payload/result_digest` | §3.9（schema `pattern`） |

> 这 12 条拒绝路径是**跨语言 conformance 的核心断言**：
> 两端必须报**同一条路径**，不是「都拒了」就算过。
>
> `invalid_audit_chain.json` 是**链校验专属负例**：它的 schema 形状完全合法
> （hash 是 64 位小写 hex，过 `pattern` 约束），**只能**靠纪律⑤的链复算被拒
> ⇒ 它同时是「链校验真的在跑」的证据（把它改回正确值就不该被拒了）。
>
> `tier_privilege_escalation.json` 同理：**schema 完全合法**（`effective_tier` 的值在枚举内），
> **只能**靠纪律⑥的裁决被拒 ⇒ 它是「权限档裁决真的在跑」的证据。
>
> **v0.4 的三条 replay 负例**同理：schema 与审计链**都完全合法**
> （`state_transition` 的载荷形状合法、哈希链重算一致），
> **只能**靠纪律⑧的重放判据被拒 ⇒ 它们是「replay 真的在跑」的证据。
>
> **v0.5 的两条负例**同理：
> `invalid_tool_annotations_mismatch.json` 的 schema 形状完全合法
> （annotations 四键齐全、值都是布尔）⇒ **只能**靠纪律⑨被拒；
> `invalid_result_digest_format.json` 的审计链**已按新载荷重算、完全合法**
> ⇒ **只能**靠 schema 的 `pattern` 被拒。

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
| 2 | 两端 `--digest` 输出逐字节一致（全部 28 份 fixture 各跑一遍：valid 14 + invalid 14） | `diff` 空 |
| 3 | 两端 `--coverage` 输出逐字节一致，且 `missing` 为空 | `diff` 空 + `missing=[]` |
| 4 | 两端 `--fields` 输出逐字节一致 | `diff` 空 |
| 5 | schema 枚举集合 ⊆ 两端枚举实现集合 | 缺一个即红 |
| 6 | Python 侧 `--schema` 自检（含三条纪律 + 五条断言）绿 | exit 0 |
| 7 | 两端 `--verify-chain` 输出逐字节一致 | `diff` 空 |
| 8 | 两端 `--replay` 输出逐字节一致（**v0.4 新增**） | `diff` 空 |
| 9 | 14 份 valid fixture 的 `x-status-transitions` 与权威表逐状态逐目标相等 | `--all` 绿（纪律④） |
| 10 | **版本四处一致**（schema.title / spec.version / status-transitions.json / README） | `--schema` 绿（断言⑥） |
| 11 | 两端 `--fields --count` 输出逐字节一致（必填字段总数自动导出） | `diff` 空 |
| 12 | **v0.4**：`outcome_unknown → running` **不在**权威表的 `transitions` 里，且**在** `forbidden` 里 | `--all` 绿 |
| 13 | **v0.4**：三条 replay 负例的期望拒绝路径精确命中 | `--all` 绿（§4.2 表格） |
| 14 | **v0.5**：`invalid_tool_annotations_mismatch.json` 被拒于 `/run/tools/0/annotations` | `--all` 绿（纪律⑨） |
| 15 | **v0.5**：`invalid_result_digest_format.json` 被拒于 `/run/audit/0/payload/result_digest` | `--all` 绿（schema `pattern`） |
| 16 | **v0.5**：两端 `tools[].annotations` 与 `tier`/`side_effect` 逐条一致（纪律⑨两端都实现） | 两端 `--all` 输出逐字节一致 |

## 6. 已知边界（v0.3 明确不支持）

* **canonical 支持这些类型**：`string` / `number` / `integer` / `boolean` / `null` / `array` / `object`。
  **v0.3 放开浮点**（args 里允许 LLM 参数）；**金额类字段（`*_usd` / `*_micro`）仍禁浮点**
  （schema 钉 `type: integer` + 校验器额外扫描兜底）。
* **浮点序列化用 ECMAScript Number::toString**（JCS §3.2.2.3），**不是** Python 的 `repr(float)`
  —— 两端必须各自实现（差异示例：`1e+16` vs `10000000000000000000`、`1e-07` vs `1e-7`、`1.0` vs `1`）。
* **两套排序口径**（别混用）：纪律⑤ 的 JCS 用 **UTF-16 码元序**；args_digest canonical 用 **码位序**。
  ASCII 键名下等价，非 ASCII 键名下分叉 ⇒ 两端各用各的（详见 §3.5 的对照表）。
* **`args_ref` 只做字符串 digest**：只对**引用字符串本身**求 digest，**不解析引用指向的内容**。
  ⇒ 引用内容变了但引用串没变，digest 不变；这是已知取舍（M2 再议）。
* **canonical 不做语义等价**：两段 JSON 若字节不同但语义相同（除本算法规定的规范化外），
  digest 仍可能不同 —— 算法是**规范化**，不是**等价归并**。
* **`args_schema` 不做完整 JSON Schema 求值**：只定粗粒度形状（`type` / `required` / `enum`），不做参数级校验。
* **`spec.framework` 不做优劣评价**：v0.3 起它是**自由字符串**（不是枚举）—— 框架清单会变，
  枚举会把消费方钉死。
* **逃生舱（`extensions` / `x-` 前缀 / `payload.extra`）不参与语义、不参与哈希**：
  里面的内容对控制面没有强制含义，跨端比对时**不要求**两端一致。

## 7. v0.3 的双端同步清单（**Java 侧待办**，TASK-035 出单时不在 Boundary 内）

> **事实**：TASK-035 的 `_Boundary:_` 只列了 `contract/**` + `tools/contract_check.py`，
> **不含 `java/control-plane/**`** ⇒ 本单落盘后**两端分叉**（实测 `mvn -o test` ⇒ 208 跑、**14 失败 1 错误**）。
> 下面是给下一单（Java 侧同步）的**逐条清单**，按「根因」归类，附实测证据。

### 7.1 Java 侧必改项（按根因分组）

| # | 根因 | Java 侧要改什么 | 实测失败信息 |
|---|---|---|---|
| 1 | **审计时间戳 `ts` → `ts_ms`**（公式变更） | `AuditChainVerifier.entryHash()` 的第 2 个数组元素：`entry.get("ts")` → `str(entry.get("ts_ms"))` | `AuditChainContractTest.recomputedHashMatchesFixtureDeclaredHash`（4 处 hash 全变） |
| 2 | **`step` 改数组** | `FieldsCollector`（`properties.step.$ref` 不存在了 ⇒ **抛异常**）、`CoverageCollector`（`doc.path("step")` 现在是数组）、`ContractCli.cmdDigest`（取第一个带 args 的 step） | `ConformanceTest.fieldsMatchesPython` ⇒ **IllegalState: schema 缺 properties.step.$ref** |
| 3 | **幂等改名**（4→2） | `CoverageCollector`（`idempotency_scope` → `dedupe_scope`）、`ToolPolicy`/`ToolCatalog`（`idempotency_key_template` → `dedupe_key_template`） | `ToolPolicyTest.toolEntriesPassContractSchema` |
| 4 | **新增必填字段** | Java 侧生成的 run 快照要补 `run.reservations[]`、`run.side_effect_unknown`，`run.error` 改可空（去掉 `code:"none"`），audit 条目改 `ts_ms` | `ApprovalResumeTest.runSnapshotPassesContractRunSchema`、`RunFinishApiTest.finishWithoutUsageKeepsBudgetUntouchedAndSnapshotStaysContractShaped` |
| 5 | **新增纪律⑥⑦** | `ContractCli.cmdAll` 接入 `effective_tier` 裁决与 `side_effect_unknown` 一致性检查（顺序：schema → 链 → 转移表 → 档位 → 副作用标志） | `ConformanceTest.allMatchesPython`（`tier_privilege_escalation.json` 在 Java 侧**没被拒** ⇒ 「本应被拒却通过了校验」） |
| 6 | **枚举集合变化** | `SchemaEnums` 补 `held`/`settled`/`released`（`reservation.state`）+ `0.2.0`/`0.3.0`（`spec.version` 版本集合） | `EnumCoverageTest.javaEnumConstantsCoverSchemaEnums` ⇒ 缺 `[0.2.0, 0.3.0, held, released, settled]` |
| 7 | **fixture 数量变化** | `ConformanceTest` 断言 15 → **21**（valid 12 + invalid 9） | `ConformanceTest.digestMatchesPythonForAllFixtures` |
| 8 | **`--coverage` 口径变化** | `CoverageCollector`：新增边界 `multi_step`/`float_in_args`/`reservation_settled`/`tier_downgraded`，新增枚举 `effective_tier`；`spec.framework` 不再是枚举 ⇒ 不再要求覆盖 | `ConformanceTest.coverageMatchesPython`（Python exit 3 ⇒ `missing` 非空） |
| 9 | **`--fields` 口径变化** | `FieldsCollector`：`step` 是数组 ⇒ 元素级必填项要下钻到 `items`（与 Python 侧 `collect_required_paths` 的新分支一致）；`--fields --count` 要支持 | `ConformanceTest.fieldsMatchesPython` |
| 10 | **`spec.version` 不再是 `const`** | `SchemaEnums` / 版本断言：`const` → 版本集合 | 见 #6 |

### 7.2 ⚠️ 一个**关键发现**：两端键序**已经一致**（不需要 Java 改键序）

用 `contract/fixtures/valid/audit_nonbmp_key.json`（payload.extra 里放了一对**会分叉**的键名：
非 BMP 的 U+1D54A `𝕊` 与 BMP 高位的 U+E000）实测：

| 口径 | 键序 | 复算出的 hash |
|---|---|---|
| Java 旧实现（`ts` 读不到 ⇒ null）+ Java 键序（码元序） | `𝕊` 在前 | `4d1f8176…eb917e`（**与 Java 报出的期望值逐字节相同**） |
| Python 新实现（`ts_ms`）+ 码元序 | `𝕊` 在前 | `7dd837d1…d1bc3bb`（fixture 里的值） |
| Python **旧**键序（码位序）+ 新公式 | U+E000 在前 | `21788b43…086e47`（**第三个数**） |

⇒ **三个 hash 互不相同**，证明：
1. Java 报出的差异**只**来自 `ts` → `ts_ms` 的**公式变更**（改公式即对齐）；
2. Python 新实现的**码元序**与 Java 的 `String.compareTo`（码元序）**已经一致** ——
   两者都把 `𝕊` 排在前；若是码位序，第三个 hash 就会出现。
3. ⇒ **Java 侧不需要改键序实现**，只要把 `ts` 改成 `str(ts_ms)` 即可对齐。

> 这条结论**修正**了 RESULT-036 §6.1「需要拍板 JCS 排序口径」的遗留：
> 口径已在 Python 侧（TASK-035 §2.15）按 RFC 8785 原文改为 **UTF-16 码元序**，
> 两端天然一致，**该遗留项就此关闭**。

## 8. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | 2026-11-02 | 首版（随 TASK-000 落盘）：协议、fixtures 清单、拒绝路径、CI 清单、已知边界 |
| v0.1 (TASK-000b) | 2026-11-02 | `capability_tier` 从 `spec` 归位到 `run.policy`（三值 `read`/`write`/`destructive`）；`x-status-transitions` 从 `run` 移到文档根；`--fields` 改为 4 键（新增 `contract`）。**5 条 invalid 的期望拒绝路径不变**，`--digest` 结果不变 |
| v0.1 (T2a 补) | 2026-11-05 | §3.1 补「**退出码纪律**」：`mvnw exec:java` 会把 ≥1 的退出码折叠成 1 ⇒ invalid 的 `exit 2` 拿不到；退出码敏感对拍改用直连 JVM 形态 `python java/control-plane/contract_check.py …`。**协议、fixtures、5 条期望路径与 `--digest` 结果均未改** |
| v0.1 (TASK-034) | 2026-11-06 | 新增**纪律④**（fixture 内嵌转移表必须与 `status-transitions.json` 逐状态逐目标相等；校验器必须真读权威表，读不到 ⇒ exit 1）与**纪律⑤**（审计链哈希公式 + genesis/链式/重算三条规则；`hash`/`prev_hash` 加 `^[0-9a-f]{64}$`）。新增 `--verify-chain` 子命令，`--all` 内含链校验与纪律④。**invalid 从 5 条增至 6 条**（新增 `invalid_audit_chain.json`，期望路径 `/run/audit/1/hash`）；**9 份 valid fixture 的内嵌转移表全部对齐权威表**（原先互相矛盾）；**2 份含 audit 的 valid fixture 的 `hash`/`prev_hash` 按新公式重算**（原先是手写占位符）。`--all` fixture 不符预期 ⇒ exit 2 |
| v0.3.0 (TASK-035) | 2026-11-07 | **契约升 v0.3.0**（Review-01 的 P0 结构问题）。① `step` 改**数组**（`minItems≥1`）；② 新增 `run.reservations[]`（reserve→settle 结构化留痕，状态 `held/settled/released/expired`）；③ 审计时间戳 `ts` → **`ts_ms`**（epoch 毫秒整数，**进哈希**，公式改 `str(ts_ms)`）⇒ **全部审计哈希作废重算**；④ `args` **放开浮点**（LLM 参数 `temperature=0.7`），**金额/预算字段仍禁浮点**；⑤ `spec.version` 由 `const` 改**版本集合** `["0.2.0","0.3.0"]`，`$id` 改可解析 URI，**版本四处一致**（新增断言⑥）；⑥ `x-status-transitions` **取消 required**（唯一权威是 `status-transitions.json`）；⑦ 新增 `run.side_effect_unknown`（`outcome_unknown` 的读侧事实）；⑧ `run.error` 改**可空**（删 `code:"none"` 哨兵）；⑨ `spec.framework` **删枚举**改自由字符串；⑩ 新增 `step[].effective_tier` + **纪律⑥**（`= min(policy.capability_tier, tool.tier)`，越权即红）；⑪ 幂等 **4→2**（`run.idempotency_key` + `step[].dedupe_scope`；`tools[].idempotency_key_template` → `dedupe_key_template`；删 `step.idempotency_key`）；⑫ 「18 fields」改**自动导出**（`--fields --count`）；⑬ 顶层加**逃生舱**（`extensions` + `x-` 前缀）；⑭ 预算加五类 `used_tokens_*` 计数器（`max_tokens` 仅为粗护栏，不证明金额）；⑮ **JCS 键序改 UTF-16 码元序**（Python 不再用 `sort_keys=True`）+ 数字改 ES Number::toString；⑯ 修过时文字（README 与 `status-transitions.json` 的「9 份互不相同」→「均对齐权威表」）。**新增纪律⑦**（`side_effect_unknown` 与 `status` 一致）。**invalid 从 6 条增至 9 条**（新增 `step_not_array` / `reservation_bad_state` / `tier_privilege_escalation`）；**valid 从 9 条增至 11 条**（新增 `run_multi_step` / `step_float_args`） |
| v0.4.0 (TASK-040) | 2026-11-07 | **契约升 v0.4.0**（语义层：让「篡改任一状态转移 ⇒ 同时破坏哈希链与转移表校验」成为机器可验证的性质）。① **权威转移表补边**：`created→waiting_approval`、`paused→failed`、`degraded→waiting_approval`、`degraded→outcome_unknown`、`outcome_unknown→cancelled`；**删边**：`outcome_unknown→running`（⚠️ **安全洞**：未经对账不得重入执行态）—— 该边在 `status-transitions.json` 的 `forbidden` 段被**显式黑名单**，校验器读到即判红；② 审计 `audit_entry.type` 由自由字符串改为**枚举**（12 值，**只增不删**），新增 `"state_transition"`；③ 新增 `$defs/state_transition_payload`（`{from,to,actor,cause,evidence_ref?}`，`actor` ∈ `control_plane/executor/human/policy`），用 `if/then` 约束「`type=="state_transition"` ⇒ payload 必须是该形状」；④ **新增 `--replay` 判据（纪律⑧，进 `--all`）**：重放状态转移序列，五条判据（首条 `from==created` / 链式连续 / 边在权威表内 / 离开 `outcome_unknown` 必带 `evidence_ref` / 状态名合法），任一不满足 ⇒ exit 2 + RFC 6901 路径；⑤ **§2.2.1 写死**「顶层 `patternProperties` + `additionalProperties:false` 的多余字段路径 = **父路径**」（Python/Java 共同口径，防止再分叉）；⑥ README 补「**终态 vs 准静态态**」区分（`outcome_unknown` 有出边但必须外部证据）；⑦ 版本四处统一 `0.4.0`（`spec.version` 集合加 `"0.4.0"`、`title`、`$id` 版本段）。**invalid 从 9 条增至 12 条**（新增 `invalid_illegal_edge` / `invalid_unknown_no_evidence` / `invalid_replay_gap`）；**valid 从 11 条增至 14 条**（新增 `step_replay` / `run_unknown_resolved`） |
| v0.5.0 (TASK-050) | 2026-11-08 | **契约升 v0.5.0**（外审建议：工具能力档要有**标准出处**、审计结果要**可复现**）。① **新增 `$defs/tool_annotations`**：`tools[].annotations` **四键必填**（`readOnlyHint/destructiveHint/idempotentHint/openWorldHint`），标 `x-source: MCP ToolAnnotations (spec 2025-03-26+)`；② **新增纪律⑨**（§3.8）：`annotations` 与 `tier`/`side_effect` 的映射规则**写死**（`tier = destructive if destructiveHint else (read if readOnlyHint else write)`；`side_effect = none if readOnlyHint else (idempotent if idempotentHint else non_idempotent)`），不一致 ⇒ 拒绝路径 `/run/tools/<i>/annotations`（**不是** `/run/tools/<i>/tier`：事实来源是注解）；`openWorldHint` **不入推导链**（它是 `outcome_unknown` 的预测变量）；③ **`audit_payload.result`（自由字符串）删除**，改 `result_digest`（`^[0-9a-f]{64}$`，§3.9）—— 自由文本参与哈希 ⇒ 同一结果换个措辞哈希就变、不可复现；摘要口径 = **与 `args_digest` 同一套 canonical**（复用实现，不另写一套）；哈希公式与 `--verify-chain`/`--replay` 逻辑**不变**，但存量 fixture 的 `hash` 须**按新载荷重算**；④ 版本四处统一 `0.5.0`。**invalid 从 12 条增至 14 条**（新增 `invalid_tool_annotations_mismatch`（期望路径 `/run/tools/0/annotations`）/ `invalid_result_digest_format`（期望路径 `/run/audit/0/payload/result_digest`））；**valid 仍 14 条**（全部补 `annotations`；`run_outcome_unknown` 加 `result_digest`） |
