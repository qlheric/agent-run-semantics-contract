# golden 输出（Java 侧权威 · 双端逐字节对拍基准）

> **本目录的每一个字节都由 Java 实现产生。**
> 任何人拿 **Python 实现**（`tools/contract_check.py`）跑同样命令，与本目录的 golden
> **逐字节 `diff` 为 0** ⇒ 双实现一致 —— **不再依赖任何一方的自述**。
>
> 关联任务：TASK-046（T15a）。协议依据：`contract/CONFORMANCE.md`。

---

## 1. 目录内容

| 路径 | 内容 | 生成命令 |
|---|---|---|
| `all.txt` | 全部 fixture 的校验结果（valid 全过 / invalid 全拒 + RFC 6901 路径） | `--all` |
| `schema.txt` | schema 自检结果 | `--schema` |
| `coverage.txt` | 枚举与边界覆盖（单行紧凑 JSON） | `--coverage` |
| `fields.txt` | 必填字段路径（单行紧凑 JSON） | `--fields` |
| `fields_count.txt` | 必填字段路径 + 一行 `count=<N>` | `--fields --count` |
| `verify_chain.txt` | 审计链复算结果（纪律⑤） | `--verify-chain` |
| `replay.txt` | 状态转移重放结果（纪律⑧） | `--replay` |
| `digests/<fixture>.json` | 每份 fixture 的 `--digest` 输出（canonical + sha256） | `--digest <fixture>` |
| `exit_codes.json` | 每个命令 / 每份 fixture 的**真实退出码** | （同上） |
| `MANIFEST.json` | 清单：`contract_version` / `java_commit` / `fixtures` / 每个输出的 sha256 | — |
| `MANIFEST.generated.txt` | 生成时间与环境（**唯一允许每次变化的文件**） | — |

**fixture 计数**：valid 14 + invalid 12 = **26** 份（契约 v0.4.0）。
`digests/` 下 26 个文件，`exit_codes.json` 与 `MANIFEST.json` 各覆盖 26 份 fixture。

---

## 2. 怎么复核（外部对拍）

### 2.1 Python 侧对拍（**核心**）

```bash
# 单条命令：stdout 与 golden 逐字节比对
python tools/contract_check.py --all | diff -u tools/golden/all.txt -
```

批量（仓库根执行，**命令 → golden 文件名**的映射必须与下表一致）：

| 命令 | golden 文件 |
|---|---|
| `--all` | `tools/golden/all.txt` |
| `--schema` | `tools/golden/schema.txt` |
| `--coverage` | `tools/golden/coverage.txt` |
| `--fields` | `tools/golden/fields.txt` |
| `--fields --count` | `tools/golden/fields_count.txt` |
| `--verify-chain` | `tools/golden/verify_chain.txt` |
| `--replay` | `tools/golden/replay.txt` |

```bash
python tools/contract_check.py --fields --count | diff -u tools/golden/fields_count.txt -
python tools/contract_check.py --verify-chain | diff -u tools/golden/verify_chain.txt -
python tools/contract_check.py --replay        | diff -u tools/golden/replay.txt -
```

fixture 的 digest 逐份对拍（26 份）：

```bash
for f in contract/fixtures/valid/*.json contract/fixtures/invalid/*.json; do
  python tools/contract_check.py --digest "$f" \
    | diff -u "tools/golden/digests/$(basename "$f")" - || echo "DIFF: $f"
done
```

> **一键自洽断言**（仓库内已落盘，只用标准库）：
>
> ```bash
> python -m unittest discover -s tests -v
> ```
>
> 它会重跑 Java CLI 与本目录 golden 逐字节比对（含退出码与 MANIFEST 的 sha256），
> 不信任仓里的文件本身。

### 2.2 Java 侧自洽（证明仓里的 golden 就是当前实现的真实输出）

```bash
pwsh -NoProfile -File tools/gen_golden.ps1 -Check
```

该模式**不重新生成**，而是重跑 Java CLI 与现有 golden 逐字节比对（含退出码与
`MANIFEST.json` 里记录的 sha256），任何一项不符即 `exit 1`。

### 2.3 再生成（**幂等**）

```bash
pwsh -NoProfile -File tools/gen_golden.ps1
```

- 一条命令重算全部 golden 并覆盖本目录；
- **离线**（Maven 一律 `-o`，不联网）；
- **幂等**：连跑两次，除 `MANIFEST.generated.txt` 的 `generated_at` 一行外，
  **所有文件逐字节相同**（已实测，见回执）。

> `MANIFEST.generated.txt` 单独拆出来，就是为了让 `MANIFEST.json` 保持**纯确定性**
> （可 `diff`、可入库、不会每次生成都产生噪声 diff）。它记录生成时间、Java 版本、
> Maven 版本、Java commit —— 这些是**环境信息**，不是判据。

---

## 3. 退出码纪律（为什么要直连 JVM）

`mvnw exec:java` 会把 ≥1 的退出码**折叠成 1**（本机 `mvn.cmd` 尾部写死
`if ERRORLEVEL 1 goto error`）⇒ 契约要求的 `exit 2` 拿不到。

因此：

- **stdout** 用任意形态对拍都行（`mvnw exec:java` 也行）；
- **退出码**必须用**直连 JVM**形态。

`tools/gen_golden.ps1` 走的就是直连 JVM：

```bash
java -cp "java/control-plane/target/classes;<deps>" \
     agentrun.controlplane.contract.ContractCli <args>
```

`exit_codes.json` 里记的就是这样跑出来的**真实退出码**：

| 命令 | 退出码 | 含义 |
|---|---|---|
| 七个命令（`--all` / `--schema` / `--coverage` / `--fields` / `--fields --count` / `--verify-chain` / `--replay`） | `0` | 全绿 |
| `--digest` 命中**契约不符**的 fixture（**7 份**：`enum_out_of_range` / `extra_property` / `float_amount` / `missing_required` / `negative_budget` / `reservation_bad_state` / `step_not_array`） | `2` | fixture 本身不合法 |
| `--digest` 其余 **19** 份 fixture | `0` | fixture 合法（invalid 里那 4 份只在**语义层**违规，schema 层仍合法 ⇒ digest 仍算得出） |

> ⚠️ **别把「invalid fixture 的 digest 退出码」当成「invalid 被拒」的证据**：
> `--digest` 只做 schema 层校验（+ 算 canonical），**不做**语义层纪律④⑤⑥⑦⑧的裁决。
> invalid fixture 的**语义层**拒绝路径在 `all.txt` 里（`rejected <path> path=<RFC6901>`）。

---

## 4. MANIFEST.json 字段说明

| 字段 | 含义 | 确定性 |
|---|---|---|
| `contract_version` | 契约版本（取自 `contract/run_semantics.schema.json` 的 `title`） | ✅ 确定性 |
| `java_commit` | 生成时仓库 HEAD 的**短** commit hash | ⚠️ 随提交变化（内容仍确定） |
| `fixtures` | fixture 总数（valid + invalid） | ✅ 确定性 |
| `commands[]` | 每个命令的 `{cmd, file, sha256}`：`cmd` = 可直接复制的命令行，`file` = 本目录下的输出文件，`sha256` = 该文件的字节 sha256 | ✅ 确定性 |
| `fixture_digests[]` | 每份 fixture 的 `{fixture, golden, exit_code, sha256}`：`fixture` = 仓库相对路径，`golden` = 本目录下的 digest 文件，`exit_code` = 该命令的真实退出码，`sha256` = digest 文件的字节 sha256 | ✅ 确定性 |

> `commands[].sha256` / `fixture_digests[].sha256` 是**文件字节**的 sha256，
> 与 golden 文件里 `--digest` 输出中的 `sha256:` **不是一回事**（后者是 canonical JSON 的摘要）。

---

## 5. 输出协议（CONFORMANCE §1 / §2.1）

golden 里的输出严格遵守：

- **UTF-8 无 BOM**、**行尾纯 LF**（Windows 默认 CRLF 会毁掉逐字节比对）；
- **键排序**、**紧凑 JSON**（`,` / `:` 后无空格）、**非 ASCII 不转义**；
- **无时间戳 / 无绝对路径 / 无随机数 / 无进程 ID**（生成脚本会主动检查绝对路径并报错）。

---

## 6. 已知边界

- golden 是 **v0.4.0** 契约的快照。契约升版本后必须**重新生成**并连同契约变更一起提交，
  否则本目录会变成过期基准。
- 本目录只覆盖**契约校验 CLI** 的输出，**不覆盖** HTTP API / MCP 代理 / 运行时的输出。
- `MANIFEST.generated.txt` 的 `java_version` / `maven` 是**本机**信息，
  换机器重生成会变 —— 它不在判据里，仅供溯源。
