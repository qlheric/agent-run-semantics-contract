#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent Run 语义契约 v0.5.0 —— Python 侧校验器 / canonical digest 实现。

用途
----
1. 校验契约自身合法性，并断言三条纪律 + 五条形状断言（additionalProperties / 整数金额 /
   状态转移表一致 / 版本四处一致 / framework 非枚举 / step 是数组 / 逃生舱）。
2. 跑全部 fixtures：valid 全过、invalid 全拒，并报 RFC 6901 路径。
3. 报枚举与边界覆盖情况。
4. 列出契约的必填字段路径（`--fields --count` 另给**自动导出**的总数）。
5. 算 args_digest（canonical JSON + sha256），供 Java 侧逐字节比对。
6. **--replay**（v0.4）：从 genesis 起重放审计链里所有 `type=="state_transition"` 条目，
   验证转移序列链式连续、每条边在权威表内、`outcome_unknown` 离开时带 `evidence_ref`。

设计纪律（双端比对的前提）
--------------------------
* 输出必须**稳定**：无时间戳、无绝对路径、键排序、LF 结尾 —— 双端比的就是字符串。
* 金额一律整数 micro-USD，禁浮点（`*_usd` / `*_micro`）；**args 允许浮点**（v0.3：
  LLM 参数如 `temperature=0.7` 必须能表达），浮点按 ECMAScript Number::toString 序列化。
* **两套排序口径，别混用**：
  - 纪律⑤ 的 JCS：**UTF-16 码元序**（RFC 8785 §3.2.3；Java `String.compareTo` 天然一致）
  - args_digest 的 canonical：**码位序**（NFC 归一化后按码位比）
* 遇到不支持的类型直接 exit 2，**不许**把它转义成 \\uXXXX 充数（那会让两端 canonical 不一致）。

用法
----
    python tools/contract_check.py --schema
    python tools/contract_check.py --all
    python tools/contract_check.py --coverage
    python tools/contract_check.py --fields --count
    python tools/contract_check.py --verify-chain
    python tools/contract_check.py --replay
    python tools/contract_check.py --digest contract/fixtures/valid/step_full.json

退出码
------
    0  通过
    1  校验失败（schema 纪律不过 / fixtures 不符预期）
    2  digest 相关错误（类型不支持、路径不存在、fixture 非法）
    3  覆盖缺口（--coverage 有 missing）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from decimal import Decimal
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量与路径（全部相对仓库根，绝不出现本机绝对路径）
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_DIR = ROOT / "contract"
SCHEMA_PATH = CONTRACT_DIR / "run_semantics.schema.json"
# 权威状态转移表（**唯一权威**，TASK-034 纪律④）：校验器必须真读它，不许静默跳过
TRANSITIONS_PATH = CONTRACT_DIR / "status-transitions.json"
VALID_DIR = CONTRACT_DIR / "fixtures" / "valid"
INVALID_DIR = CONTRACT_DIR / "fixtures" / "invalid"

# 审计链链首条目的 prev_hash（64 个 0，TASK-034 §2.2 genesis 规则）
ZERO_HASH = "0" * 64

# 输出编码固定为 UTF-8 + LF（Windows 下 print 默认 CRLF，会破坏逐字节比对）
try:
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stderr.reconfigure(encoding="utf-8", newline="\n")
except Exception:  # pragma: no cover - 极老解释器兜底
    pass

# 枚举全集（用于覆盖判定与状态转移表一致性检查）
RUN_STATUS_VALUES = [
    "created",
    "running",
    "waiting_approval",
    "paused",
    "degraded",
    "succeeded",
    "failed",
    "cancelled",
    "outcome_unknown",
]
FRAMEWORK_VALUES = ["langgraph", "agent_framework", "custom"]
# 能力上限档：语义 5 的权限档（read/write/destructive），归 run.policy
CAPABILITY_TIER_VALUES = ["read", "write", "destructive"]
RETRY_PROFILE_VALUES = ["model_only", "read_only", "full"]
SIDE_EFFECT_VALUES = ["none", "idempotent", "non_idempotent"]
APPROVAL_STATUS_VALUES = ["pending", "approved", "rejected", "expired"]
CLOCK_VALUES = ["wall", "monotonic"]


class ContractError(Exception):
    """契约校验失败（携带人可读原因）。"""


class DigestError(Exception):
    """digest 计算失败（浮点 / 路径不存在 / fixture 非法）。"""


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def out(line: str = "") -> None:
    """写一行到 stdout（UTF-8 / LF）。"""
    sys.stdout.write(line + "\n")


def rel(path: Path) -> str:
    """转成相对仓库根的 POSIX 风格路径（输出里不许出现绝对路径）。"""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def resolve_ref(schema: dict, ref: str) -> dict:
    """解析本地 `#/$defs/xxx` 引用。"""
    if not ref.startswith("#/"):
        raise ContractError(f"不支持的 $ref: {ref}")
    node = schema
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def is_object_schema(node: dict) -> bool:
    """判断一个 schema 节点是否是 object 类型（type 可为列表）。"""
    if not isinstance(node, dict):
        return False
    t = node.get("type")
    if t == "object":
        return True
    return isinstance(t, list) and "object" in t


def walk_schemas(schema: dict):
    """遍历 schema 内所有节点（含 $defs）。"""
    yield schema
    for key, value in schema.items():
        if isinstance(value, dict):
            yield from walk_schemas(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield from walk_schemas(item)


# ---------------------------------------------------------------------------
# 校验器封装
# ---------------------------------------------------------------------------


def get_validator_class():
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover
        raise ContractError(f"缺少依赖 jsonschema（本机应已装 4.26.0）：{exc}") from exc
    return Draft202012Validator


class FakeError:
    """自造错误（用于 schema 之外的浮点扫描），接口对齐 jsonschema 的 Error。"""

    def __init__(self, path: str, message: str, validator: str = "no-float"):
        self.absolute_path = [p for p in path.split("/") if p]
        self.message = message
        self.validator = validator

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FakeError {self.message}>"


def pointer_of(error) -> str:
    """把校验错误转成 RFC 6901 JSON Pointer。"""
    parts = []
    for token in error.absolute_path:
        parts.append(str(token).replace("~", "~0").replace("/", "~1"))
    pointer = "/" + "/".join(parts) if parts else ""
    message = error.message
    if error.validator == "required":
        m = re.search(r"'([^']+)' is a required property", message)
        if m:
            pointer = pointer + "/" + m.group(1).replace("~", "~0").replace("/", "~1")
    elif error.validator == "additionalProperties":
        m = re.search(r"'([^']+)' was unexpected", message)
        if m:
            pointer = pointer + "/" + m.group(1).replace("~", "~0").replace("/", "~1")
    return pointer or "/"


def scan_floats(node, pointer: str = "", found=None):
    """扫描整份文档里的浮点数（**金额禁浮点**纪律的兜底检查）。

    v0.3 口径（TASK-035 §2.4）：**只有金额/预算字段禁浮点**（`*_usd` / `*_micro`），
    其余位置（尤其 args 里的 LLM 参数如 `temperature=0.7`）**允许 I-JSON double**。
    ⇒ 禁浮点不再是「全文档禁」，本函数只盯金额字段。

    为什么还要显式兜底：jsonschema 对 `1.0` 这类值的整数判定依赖具体实现，
    这里显式扫一遍，保证双端行为一致。
    """
    if found is None:
        found = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{pointer}/{key}"
            if _is_money_field(key) and isinstance(value, float):
                found.append(FakeError(child, f"{value} 是浮点数，金额必须是整数 micro-USD"))
            scan_floats(value, child, found)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            scan_floats(value, f"{pointer}/{index}", found)
    return found


# 金额类字段名后缀：v0.3 起只有这些禁浮点（其余字段允许 double）
MONEY_FIELD_SUFFIXES = ("_usd", "_micro")


def _is_money_field(name: str) -> bool:
    """字段名是否是金额类（`*_usd` / `*_micro`）—— 只有这类禁浮点。"""
    return name.endswith(MONEY_FIELD_SUFFIXES)


def validate_doc(schema: dict, doc) -> list:
    """校验一份文档，返回错误列表（schema 错误 + 浮点扫描错误，按 pointer 去重）。"""
    validator_cls = get_validator_class()
    validator = validator_cls(schema)
    errors = list(validator.iter_errors(doc))
    errors.extend(scan_floats(doc))
    seen = set()
    unique = []
    for err in errors:
        key = (pointer_of(err), err.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(err)
    unique.sort(key=lambda e: (-len(e.absolute_path), pointer_of(e), e.message))
    return unique


# ---------------------------------------------------------------------------
# 子命令 1：--schema
# ---------------------------------------------------------------------------


def check_schema_discipline(schema: dict) -> list:
    """三条纪律 + 五条形状断言（v0.3）：
    ① 每个 object 都有 additionalProperties: false（显式标注 x-free-form 的自由载荷容器豁免）；
    ② 所有 `*_usd` 字段 type 为 integer；
    ③ x-status-transitions 的 key/value 都在 run.status 枚举内（转移表在**文档根**）；
    ④ $defs/spec 不含 capability_tier（自造轴已删）；
    ⑤ $defs/policy 含 capability_tier 且 enum 恰为 ["read","write","destructive"]；
    ⑥ **版本四处一致**（title / spec.version / status-transitions.version / README）；
    ⑦ `spec.framework` 是自由字符串（**不许**再是枚举）；
    ⑧ `step` 是数组（items 指向 $defs/step，minItems ≥ 1）；
    ⑨ 顶层允许逃生舱（extensions + `x-` 前缀 patternProperties）。
    """
    problems = []

    # ① additionalProperties
    for node in walk_schemas(schema):
        if not is_object_schema(node):
            continue
        if node.get("x-free-form") is True:
            continue  # 自由载荷容器（args）：字段名不可枚举，豁免并要求禁浮点
        if node.get("additionalProperties") is not False:
            label = node.get("title") or node.get("description") or "object"
            problems.append(f"纪律①：object 未声明 additionalProperties:false（{label}）")

    # ② 金额整数
    for node in walk_schemas(schema):
        for name, prop in (node.get("properties") or {}).items():
            if not isinstance(prop, dict):
                continue
            if name.endswith("_usd"):
                if prop.get("type") != "integer":
                    problems.append(
                        f"纪律②：字段 {name} 的 type 必须是 integer（实际 {prop.get('type')!r}）"
                    )

    # ③ 状态转移表一致（以 run 的 status 枚举为准，不靠遍历猜；表挂在文档根）
    run_ref = ((schema.get("properties") or {}).get("run") or {}).get("$ref")
    if not run_ref:
        problems.append("纪律③：顶层 properties.run 缺 $ref")
        return problems
    run_node = resolve_ref(schema, run_ref)
    status_prop = (run_node.get("properties") or {}).get("status") or {}
    status_enum = status_prop.get("enum")
    if not status_enum:
        problems.append("纪律③：找不到 run.status 枚举")
        return problems
    transition_node = (schema.get("properties") or {}).get("x-status-transitions")
    if transition_node is None:
        problems.append("纪律③：文档根找不到 x-status-transitions 定义")
        return problems
    if "$ref" in transition_node:
        transition_node = resolve_ref(schema, transition_node["$ref"])
    enum_set = set(status_enum)
    key_set = set((transition_node.get("properties") or {}).keys())
    if key_set != enum_set:
        problems.append(
            "纪律③：x-status-transitions 的 key 与 run.status 枚举不一致"
            f"（缺 {sorted(enum_set - key_set)}，多 {sorted(key_set - enum_set)}）"
        )
    for key, value in (transition_node.get("properties") or {}).items():
        targets = resolve_ref(schema, value["$ref"]) if "$ref" in value else value
        for item in targets.get("items", {}).get("enum", []) or []:
            if item not in enum_set:
                problems.append(f"纪律③：x-status-transitions.{key} 出现枚举外取值 {item!r}")

    # ④ capability_tier 不许再挂在 spec 上（自造轴 basic/standard/elevated 已废）
    defs = schema.get("$defs") or {}
    spec_node = defs.get("spec") or {}
    spec_props = spec_node.get("properties") or {}
    if "capability_tier" in spec_props:
        problems.append("断言④：$defs/spec 不该再含 capability_tier（应归 run.policy）")
    if "capability_tier" in (spec_node.get("required") or []):
        problems.append("断言④：$defs/spec.required 不该再含 capability_tier")

    # ⑤ capability_tier 必须在 policy 上，且枚举恰为 read/write/destructive
    policy_node = defs.get("policy") or {}
    policy_props = policy_node.get("properties") or {}
    tier_prop = policy_props.get("capability_tier")
    if not isinstance(tier_prop, dict):
        problems.append("断言⑤：$defs/policy 缺 capability_tier")
    else:
        if tier_prop.get("type") != "string":
            problems.append(
                f"断言⑤：policy.capability_tier 的 type 必须是 string（实际 {tier_prop.get('type')!r}）"
            )
        if tier_prop.get("enum") != CAPABILITY_TIER_VALUES:
            problems.append(
                f"断言⑤：policy.capability_tier 的 enum 必须是 {CAPABILITY_TIER_VALUES}"
                f"（实际 {tier_prop.get('enum')!r}）"
            )
    if "capability_tier" not in (policy_node.get("required") or []):
        problems.append("断言⑤：$defs/policy.required 必须含 capability_tier")

    # ⑥ 版本四处一致（title / spec.version / status-transitions.version / README）
    problems.extend(check_version_consistency(schema))

    # ⑦ spec.framework 是自由字符串，不许再是枚举
    framework = spec_props.get("framework") or {}
    if "enum" in framework:
        problems.append("断言⑦：spec.framework 不该再是枚举（框架清单会变，会把消费方钉死）")
    if framework.get("type") != "string":
        problems.append(
            f"断言⑦：spec.framework 的 type 必须是 string（实际 {framework.get('type')!r}）"
        )

    # ⑧ step 是数组
    step_node = (schema.get("properties") or {}).get("step") or {}
    if step_node.get("type") != "array":
        problems.append(f"断言⑧：顶层 step 必须是 array（实际 {step_node.get('type')!r}）")
    else:
        items = step_node.get("items") or {}
        if items.get("$ref") != "#/$defs/step":
            problems.append(f"断言⑧：step.items 必须指向 #/$defs/step（实际 {items.get('$ref')!r}）")
        min_items = step_node.get("minItems")
        if not isinstance(min_items, int) or min_items < 1:
            problems.append(f"断言⑧：step.minItems 必须 ≥ 1（实际 {min_items!r}）")

    # ⑨ 逃生舱：顶层允许 extensions + `x-` 前缀字段
    if "extensions" not in (schema.get("properties") or {}):
        problems.append("断言⑨：顶层 properties 缺 extensions 逃生舱")
    pattern_props = schema.get("patternProperties") or {}
    if not any(str(key).startswith("^x-") for key in pattern_props):
        problems.append("断言⑨：顶层缺 `x-` 前缀的 patternProperties 逃生舱")
    return problems


# ---------------------------------------------------------------------------
# 版本一致性（v0.3 · 评审 (17)）
# ---------------------------------------------------------------------------

# 契约当前版本（唯一真值；四处必须与它一致）
CURRENT_SPEC_VERSION = "0.5.0"
README_PATH = CONTRACT_DIR / "README.md"


def extract_version_from_schema(schema: dict):
    """从 schema 取当前版本：`spec.version` 是**版本集合**（enum），取集合里最大者。"""
    defs = schema.get("$defs") or {}
    spec_node = defs.get("spec") or {}
    version_prop = (spec_node.get("properties") or {}).get("version") or {}
    versions = version_prop.get("enum")
    if isinstance(versions, list) and versions:
        # 取语义化版本最大者
        return max(versions, key=_semver_key)
    if version_prop.get("const"):
        return version_prop["const"]
    return None


def _semver_key(text: str):
    """语义化版本排序键（'0.10.0' > '0.9.0'，不能按字符串比）。"""
    parts = []
    for chunk in str(text).split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def extract_version_from_title(schema: dict):
    """从 schema title 里取版本（如 'Agent Run 语义契约 v0.3.0'）。"""
    title = schema.get("title") or ""
    match = re.search(r"v?(\d+\.\d+\.\d+)", title)
    return match.group(1) if match else None


def extract_version_from_transitions() -> str:
    """从权威转移表读 version 字段（去掉 `v` 前缀，统一成 `X.Y.Z`）。"""
    try:
        with TRANSITIONS_PATH.open("r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    version = doc.get("version")
    if not isinstance(version, str):
        return None
    return version[1:] if version.startswith("v") else version


def extract_version_from_readme():
    """从 README 里取版本（'版本 `0.3.0`' 这行）。"""
    try:
        text = README_PATH.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"版本\s*`?v?(\d+\.\d+\.\d+)`?", text)
    return match.group(1) if match else None


def check_version_consistency(schema: dict) -> list:
    """断言⑥：版本**四处一致**。

    四处 = ① schema.title ② schema.spec.version（版本集合的当前值）
           ③ contract/status-transitions.json 的 version ④ contract/README.md 的版本行

    任一处缺失或与其它处不等 ⇒ 判红。
    """
    problems = []
    found = {
        "schema.title": extract_version_from_title(schema),
        "schema.spec.version": extract_version_from_schema(schema),
        "status-transitions.json": extract_version_from_transitions(),
        "README.md": extract_version_from_readme(),
    }
    missing = [name for name, value in found.items() if not value]
    if missing:
        problems.append(f"断言⑥：版本四处一致 —— 读不到版本：{missing}")
        return problems
    distinct = sorted(set(found.values()))
    if len(distinct) != 1:
        detail = "，".join(f"{name}={value}" for name, value in found.items())
        problems.append(f"断言⑥：版本四处不一致（{detail}）")
        return problems
    if distinct[0] != CURRENT_SPEC_VERSION:
        problems.append(
            f"断言⑥：版本须为 {CURRENT_SPEC_VERSION}（实际 {distinct[0]}）"
        )
    return problems


def cmd_schema(_args) -> int:
    try:
        schema = load_json(SCHEMA_PATH)
    except FileNotFoundError:
        out(f"schema file not found: {rel(SCHEMA_PATH)}")
        return 1
    except json.JSONDecodeError as exc:
        out(f"schema 不是合法 JSON：{exc}")
        return 1

    validator_cls = get_validator_class()
    try:
        validator_cls.check_schema(schema)
    except Exception as exc:
        out(f"schema 自身不合法：{exc}")
        return 1

    problems = check_schema_discipline(schema)
    if problems:
        for p in problems:
            out(f"schema discipline failed: {p}")
        return 1

    out("schema ok")
    return 0


# ---------------------------------------------------------------------------
# 权威状态转移表（TASK-034 纪律④）
# ---------------------------------------------------------------------------


def load_authority_transitions() -> dict:
    """读**唯一权威**状态转移表 contract/status-transitions.json。

    返回 {state: sorted(targets)}（目标已排序，便于与 fixture 逐状态逐目标比较）。

    文件缺失 / JSON 坏 / 结构不对 ⇒ 抛 ContractError（调用方 exit 1）。
    **不许静默跳过**：转移表读不到 ⇒ 纪律④根本无法执行 ⇒ 直接判红。

    **v0.4：黑名单强制** —— `forbidden` 段里列出的边**不许**出现在 `transitions` 里。
    这条把「`outcome_unknown → running` 是安全洞」从散文变成机器可验证的性质：
    有人把它加回去 ⇒ 读表即判红（不是「悄悄生效」）。
    """
    try:
        with TRANSITIONS_PATH.open("r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError as exc:
        raise ContractError(f"权威状态转移表不存在：{rel(TRANSITIONS_PATH)}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"权威状态转移表不是合法 JSON：{exc}") from exc

    if not isinstance(doc, dict):
        raise ContractError(f"权威状态转移表不是 JSON 对象：{rel(TRANSITIONS_PATH)}")
    states = doc.get("states")
    transitions = doc.get("transitions")
    if not isinstance(states, list) or not states:
        raise ContractError(f"权威状态转移表缺非空 states 数组：{rel(TRANSITIONS_PATH)}")
    if not isinstance(transitions, dict) or not transitions:
        raise ContractError(f"权威状态转移表缺非空 transitions 对象：{rel(TRANSITIONS_PATH)}")

    authority: dict = {}
    for state in states:
        if not isinstance(state, str):
            raise ContractError(f"权威状态转移表的 states 含非字符串项：{state!r}")
        targets = transitions.get(state)
        if not isinstance(targets, list):
            raise ContractError(
                f"权威状态转移表的 transitions.{state} 必须是数组（空数组 = 终态）：{rel(TRANSITIONS_PATH)}"
            )
        for target in targets:
            if not isinstance(target, str):
                raise ContractError(f"权威状态转移表的 transitions.{state} 含非字符串目标：{target!r}")
        authority[state] = sorted(targets)

    check_forbidden_edges(doc, authority)
    return authority


def check_forbidden_edges(doc: dict, authority: dict) -> None:
    """v0.4：`forbidden` 段里的边**不许**出现在 `transitions` 里 ⇒ 违反即判红。

    这是「安全洞不许复现」的机器可验证保证：把 `outcome_unknown → running`
    加回权威表 ⇒ 本函数抛 ContractError ⇒ 校验器 exit 1（而不是照常跑完）。
    """
    forbidden = doc.get("forbidden")
    if forbidden is None:
        return
    if not isinstance(forbidden, list):
        raise ContractError(f"权威状态转移表的 forbidden 必须是数组：{rel(TRANSITIONS_PATH)}")
    for item in forbidden:
        if not isinstance(item, dict):
            raise ContractError(
                f"权威状态转移表的 forbidden 含非对象项：{item!r}（{rel(TRANSITIONS_PATH)}）"
            )
        frm = item.get("from")
        to = item.get("to")
        if not isinstance(frm, str) or not isinstance(to, str):
            raise ContractError(
                f"权威状态转移表的 forbidden 条目必须含字符串 from/to：{item!r}"
                f"（{rel(TRANSITIONS_PATH)}）"
            )
        if to in authority.get(frm, []):
            raise ContractError(
                f"权威状态转移表出现被禁止的边 {frm} → {to}"
                f"（{rel(TRANSITIONS_PATH)} 的 forbidden 段已把它列入黑名单）"
            )


def check_fixture_transitions(doc, authority: dict) -> list:
    """纪律④：fixture 根级 x-status-transitions 必须与权威表**逐状态、逐目标（排序后）相等**。

    返回差异列表 [(pointer, message)]；pointer 形如 /x-status-transitions/<state>。
    message 贴出差异：state: fixture=… authority=…
    """
    table = doc.get("x-status-transitions")
    if not isinstance(table, dict):
        # 缺失/形状不对由 schema 负责报错（required + additionalProperties:false），此处不重复
        return []

    problems = []
    for state in sorted(set(table) | set(authority)):
        fixture_targets = table.get(state)
        authority_targets = authority.get(state)
        if fixture_targets is None:
            problems.append((
                f"/x-status-transitions/{state}",
                f"与权威表不一致 state: fixture=（缺该状态） authority={json.dumps(authority_targets, ensure_ascii=False)}",
            ))
            continue
        if not isinstance(fixture_targets, list):
            continue  # 形状不对交给 schema 报错
        fixture_sorted = sorted(fixture_targets)
        if fixture_sorted != authority_targets:
            problems.append((
                f"/x-status-transitions/{state}",
                "与权威表不一致 state: fixture="
                f"{json.dumps(fixture_sorted, ensure_ascii=False)}"
                f" authority={json.dumps(authority_targets, ensure_ascii=False)}",
            ))
    return problems


# ---------------------------------------------------------------------------
# 审计链（TASK-034 纪律⑤）
# ---------------------------------------------------------------------------


def jcs(value) -> str:
    """JCS（RFC 8785）序列化 —— 本结构下的实现。

    公式（CONFORMANCE 纪律⑤）：
        canonical = JCS([ str(seq), str(ts_ms), type, run_id, step_id, str(attempt), JCS(payload), prev_hash ])
        hash      = sha256( canonical.encode("utf-8") ).hexdigest()

    **v0.3 的两条口径变更**（TASK-035 §2.15 / §2.4）：
    1. **键序 = UTF-16 码元序**（RFC 8785 §3.2.3 原文口径），**不是** Python 的码位序。
       Java 的 `String.compareTo` 天然就是 UTF-16 码元序 ⇒ 两端天然一致，
       不再依赖「键名都是 ASCII」这个前提。
       实现：`sorted(keys, key=lambda k: k.encode("utf-16-be", "surrogatepass"))`
       —— 按 UTF-16 码元（大端）逐字节比较，等价于 Java 的 UTF-16 码元序。
    2. **数字按 ECMAScript Number::toString 序列化**（JCS §3.2.2.3）。
       Python 的 `repr(float)` 与 ES 不同（`1e+16` vs `10000000000000000000`、
       `1e-07` vs `1e-7`、`1.0` vs `1`）⇒ 必须自己实现 ES 算法，否则两端会分叉。

    其余口径不变：
    * 顶层是数组、数组元素全是字符串 ⇒ 数组保序；
    * 无多余空白（separators=(",", ":")）；
    * 非 ASCII 原样输出 UTF-8（禁 \\uXXXX 转义）；
    * 字符串转义只针对 `"`、`\\` 与 C0 控制字符（与 Python json.encoder 一致）。
    """
    return _jcs_write(value)


# JCS 键序：UTF-16 码元序（RFC 8785 §3.2.3）。Java 的 String.compareTo 是同一口径。
def _utf16_sort_key(name: str) -> bytes:
    """UTF-16 码元序排序键（大端编码，逐码元比较）。"""
    return name.encode("utf-16-be", "surrogatepass")


def _es_number(value: float) -> str:
    """按 ECMAScript Number::toString 序列化一个 double（JCS §3.2.2.3）。

    与 Python `repr(float)` 的差异（本项目踩过的坑）：
        1e+16  → ES "10000000000000000000"（Python repr "1e+16"）
        1e-7   → ES "1e-7"            （Python repr "1e-07"）
        1.0    → ES "1"               （Python repr "1.0"）

    算法（ECMA-262 §6.1.6.1.20）：
        1. 取最短往返十进制表示 ⇒ 数字串 s（k 位）与十进制指数 e，使 x = s × 10^e；
        2. 去掉 s 的尾随零（同步调整 e），令 n = k + e（即 x = s × 10^(n-k)）；
        3. k ≤ n ≤ 21        ⇒ s + (n-k) 个 0
           0 < n ≤ 21        ⇒ s 的前 n 位 + "." + 其余
           -6 < n ≤ 0        ⇒ "0." + (-n) 个 0 + s
           其余              ⇒ 指数形：k==1 时 "s" + "e" + (n-1)；否则 "s[0].s[1:]" + "e" + (n-1)
    """
    if value != value:  # NaN
        raise DigestError("NaN 不能进入 canonical")
    if value in (float("inf"), float("-inf")):
        raise DigestError("Infinity 不能进入 canonical")
    if value == 0:
        return "0"  # ES：0 与 -0 都序列化成 "0"
    if value < 0:
        return "-" + _es_number(-value)

    # 最短往返十进制表示（Python 的 repr 已是最短往返）⇒ 拆成 (数字串, 指数)
    dec = Decimal(repr(value))
    _, digits, exponent = dec.as_tuple()
    s = int("".join(str(d) for d in digits))
    e = int(exponent)
    while s % 10 == 0:
        s //= 10
        e += 1
    ds = str(s)
    k = len(ds)
    n = k + e  # x = s × 10^(n-k)

    if k <= n <= 21:
        return ds + "0" * (n - k)
    if 0 < n <= 21:
        return ds[:n] + "." + ds[n:]
    if -6 < n <= 0:
        return "0." + "0" * (-n) + ds
    # 指数形
    exp = n - 1
    sign = "+" if exp >= 0 else "-"
    if k == 1:
        return ds + "e" + sign + str(abs(exp))
    return ds[0] + "." + ds[1:] + "e" + sign + str(abs(exp))


def _jcs_write(value) -> str:
    """JCS 序列化器：UTF-16 码元序键排序 + ES 数字 + 紧凑分隔符 + 非 ASCII 不转义。"""
    out: list = []
    _jcs_emit(value, out)
    return "".join(out)


def _jcs_emit(value, out: list) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        out.append(str(value))
    elif isinstance(value, float):
        out.append(_es_number(value))
    elif isinstance(value, str):
        out.append(json.encoder.encode_basestring(value))
    elif isinstance(value, list):
        out.append("[")
        for index, item in enumerate(value):
            if index:
                out.append(",")
            _jcs_emit(item, out)
        out.append("]")
    elif isinstance(value, dict):
        out.append("{")
        for index, key in enumerate(sorted(value.keys(), key=_utf16_sort_key)):
            if index:
                out.append(",")
            out.append(json.encoder.encode_basestring(key))
            out.append(":")
            _jcs_emit(value[key], out)
        out.append("}")
    else:
        raise DigestError(f"不支持的类型 {type(value).__name__}")


def audit_entry_hash(entry: dict, prev_hash: str) -> str:
    """按纪律⑤公式重算一条审计条目的 hash。

    公式（v0.3：时间戳改 ts_ms 整数，**参与哈希**）：
        canonical = JCS([ str(seq), str(ts_ms), type, run_id, step_id, str(attempt),
                          JCS(payload), prev_hash ])
        hash      = sha256( canonical.encode("utf-8") ).hexdigest()
    """
    canonical = jcs([
        str(entry.get("seq")),
        str(entry.get("ts_ms")),
        entry.get("type"),
        entry.get("run_id"),
        entry.get("step_id"),
        str(entry.get("attempt")),
        jcs(entry.get("payload") if isinstance(entry.get("payload"), dict) else {}),
        prev_hash,
    ])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_audit_chain(doc) -> list:
    """复算 run.audit 的哈希链，返回错误列表 [(pointer, message)]。

    规则（CONFORMANCE 纪律⑤）：
      1. 第 1 条 prev_hash = 64 个 0（genesis）；
      2. entry[i].prev_hash == entry[i-1].hash（链式）；
      3. entry[i].hash == 按公式重算的值（不符 ⇒ pointer /run/audit/<i>/hash）。
    """
    audit = (doc.get("run") or {}).get("audit")
    if not isinstance(audit, list):
        return []

    problems = []
    prev = ZERO_HASH
    for index, entry in enumerate(audit):
        if not isinstance(entry, dict):
            continue
        pointer = f"/run/audit/{index}"
        declared_prev = entry.get("prev_hash")
        if declared_prev != prev:
            problems.append((
                f"{pointer}/prev_hash",
                f"prev_hash 断链：期望 {prev}，实际 {declared_prev}",
            ))
        expected = audit_entry_hash(entry, prev)
        actual = entry.get("hash")
        if expected != actual:
            problems.append((
                f"{pointer}/hash",
                f"hash 与重算结果不一致：期望 {expected}，实际 {actual}",
            ))
        # 链式推进用**字面 hash**（上一条写的是什么就接什么），
        # 这样篡改单条 hash 时只报那一条，不会连累下一条误报 prev_hash 断链。
        prev = actual if isinstance(actual, str) else expected
    return problems


# ---------------------------------------------------------------------------
# 权限档裁决（v0.3 · 评审 (12)）
# ---------------------------------------------------------------------------

# 权限档的偏序：read < write < destructive（数值小 = 权限小）
TIER_ORDER = {"read": 0, "write": 1, "destructive": 2}


def check_effective_tier(doc) -> list:
    """断言：每个 step 的 `effective_tier` 必须等于 min(policy.capability_tier, tool.tier)。

    冲突裁决写死（TASK-035 §2.10）：`effective_tier = min(policy.capability_tier, tool.tier)`，
    顺序 `read < write < destructive`。
    越权（policy=read 而 tool=destructive 却写成 destructive）⇒ 判红。

    返回错误列表 [(pointer, message)]；pointer 形如 `/step/0/effective_tier`。
    """
    problems = []
    run = doc.get("run") or {}
    policy = run.get("policy") or {}
    policy_tier = policy.get("capability_tier")
    if policy_tier not in TIER_ORDER:
        return problems  # 形状不对交给 schema 报错

    tool_tiers = {}
    for tool in run.get("tools") or []:
        if isinstance(tool, dict) and tool.get("tool") is not None:
            tool_tiers[tool["tool"]] = tool.get("tier")

    steps = doc.get("step")
    if not isinstance(steps, list):
        return problems  # 形状不对交给 schema 报错

    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        declared = step.get("effective_tier")
        if declared not in TIER_ORDER:
            continue  # 形状不对交给 schema 报错
        tool_name = (step.get("grant_request") or {}).get("tool")
        tool_tier = tool_tiers.get(tool_name)
        if tool_tier not in TIER_ORDER:
            # 工具不在目录里（或档位非法）⇒ 无法裁决，交给 schema 报错
            continue
        expected = (policy_tier if TIER_ORDER[policy_tier] <= TIER_ORDER[tool_tier] else tool_tier)
        if declared != expected:
            problems.append((
                f"/step/{index}/effective_tier",
                f"effective_tier 越权或写错：期望 min(policy={policy_tier}, tool={tool_tier})"
                f"={expected}，实际 {declared}",
            ))
    return problems


def check_side_effect_unknown(doc) -> list:
    """断言：`run.side_effect_unknown` 与 `run.status` 一致（outcome_unknown ⇔ true）。

    这是 `outcome_unknown` 的**读侧事实**（TASK-035 §2.7）：
    status=outcome_unknown ⇒ side_effect_unknown=true；其余状态 ⇒ false。
    """
    problems = []
    run = doc.get("run") or {}
    status = run.get("status")
    declared = run.get("side_effect_unknown")
    if not isinstance(declared, bool):
        return problems  # 形状不对交给 schema 报错
    expected = (status == "outcome_unknown")
    if declared != expected:
        problems.append((
            "/run/side_effect_unknown",
            f"与 run.status 不一致：status={status} ⇒ side_effect_unknown 应为 "
            f"{str(expected).lower()}，实际 {str(declared).lower()}",
        ))
    return problems


# ---------------------------------------------------------------------------
# 纪律⑨（v0.5 · 工具注解折叠 MCP ToolAnnotations）
# ---------------------------------------------------------------------------

# MCP ToolAnnotations 的四个 hint（契约 $defs/tool_annotations；四键必填）
ANNOTATION_KEYS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")


def expected_tier_from_annotations(annotations: dict) -> str:
    """按注解推 tier（映射规则写死，CONFORMANCE §3.8）。

    `tier = destructive if destructiveHint else (read if readOnlyHint else write)`
    """
    if annotations.get("destructiveHint"):
        return "destructive"
    if annotations.get("readOnlyHint"):
        return "read"
    return "write"


def expected_side_effect_from_annotations(annotations: dict) -> str:
    """按注解推 side_effect（映射规则写死，CONFORMANCE §3.8）。

    `side_effect = none if readOnlyHint else (idempotent if idempotentHint else non_idempotent)`
    """
    if annotations.get("readOnlyHint"):
        return "none"
    if annotations.get("idempotentHint"):
        return "idempotent"
    return "non_idempotent"


def check_tool_annotations(doc) -> list:
    """纪律⑨：`tools[].annotations` 与 `tier` / `side_effect` 必须一致。

    映射规则（写死，两端一致）：
      * `tier = destructive if destructiveHint else (read if readOnlyHint else write)`
      * `side_effect = none if readOnlyHint else (idempotent if idempotentHint else non_idempotent)`

    不一致 ⇒ 判红，路径 `/run/tools/<i>/annotations`（**不是** `/run/tools/<i>/tier` ——
    判定的事实来源是注解，tier 只是被推导出来的那一端）。

    形状不对（缺 hint、hint 不是布尔）⇒ **不重复报错**，交给 schema 报错
    （否则同一处问题报两遍、路径不一致 ⇒ 双端输出分叉）。
    """
    problems = []
    tools = (doc.get("run") or {}).get("tools")
    if not isinstance(tools, list):
        return problems  # 形状不对交给 schema 报错

    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            continue
        annotations = tool.get("annotations")
        if not isinstance(annotations, dict):
            continue  # 形状不对交给 schema 报错
        if any(not isinstance(annotations.get(key), bool) for key in ANNOTATION_KEYS):
            continue  # 形状不对交给 schema 报错

        expected_tier = expected_tier_from_annotations(annotations)
        expected_side_effect = expected_side_effect_from_annotations(annotations)
        declared_tier = tool.get("tier")
        declared_side_effect = tool.get("side_effect")

        mismatches = []
        if declared_tier in TIER_ORDER and declared_tier != expected_tier:
            mismatches.append(f"tier 应为 {expected_tier}，实际 {declared_tier}")
        if declared_side_effect in SIDE_EFFECT_VALUES and declared_side_effect != expected_side_effect:
            mismatches.append(
                f"side_effect 应为 {expected_side_effect}，实际 {declared_side_effect}")
        if mismatches:
            problems.append((
                f"/run/tools/{index}/annotations",
                "annotations 与 tier/side_effect 不一致：" + "；".join(mismatches),
            ))
    return problems


# ---------------------------------------------------------------------------
# --replay（v0.4 · 语义层重放判据）
# ---------------------------------------------------------------------------

# 审计条目的类型：状态转移留痕
STATE_TRANSITION_TYPE = "state_transition"


def check_replay(doc, authority: dict) -> list:
    """从 genesis 起重放所有 `type=="state_transition"` 的审计条目。

    **五条判据**（任一不满足 ⇒ 判红并给 RFC 6901 路径）：

    1. 第一条的 `from` 必须是 `created`（run 的初始状态）；
    2. 后一条的 `from` 必须等于前一条的 `to`（**链式连续性**）；
    3. 每条 `from→to` 必须在**权威转移表**里合法；
    4. `from == outcome_unknown` 时 `payload.evidence_ref` **必填**
       （离开未知态必须有外部对账证据 ⇒ 缺失报 `/run/audit/<i>/payload/evidence_ref`）；
    5. `from`/`to` 必须是合法状态名（枚举外 ⇒ 非法边）。

    返回错误列表 [(pointer, message)]；pointer 形如
    `/run/audit/<i>/payload`、`/run/audit/<i>/payload/from`、`/run/audit/<i>/payload/evidence_ref`。
    """
    problems = []
    audit = (doc.get("run") or {}).get("audit")
    if not isinstance(audit, list):
        return problems

    # 只取 state_transition 条目（其余审计类型不参与重放）
    transitions = []
    for index, entry in enumerate(audit):
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != STATE_TRANSITION_TYPE:
            continue
        transitions.append((index, entry))

    if not transitions:
        return problems

    previous_to = None
    for index, entry in transitions:
        pointer = f"/run/audit/{index}/payload"
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            problems.append((pointer, "state_transition 的 payload 必须是对象"))
            continue
        frm = payload.get("from")
        to = payload.get("to")

        # --- 判据 1：第一条的 from 必须是 created ---
        if previous_to is None:
            if frm != "created":
                problems.append((
                    f"{pointer}/from",
                    f"state_transition 序列的第一条 from 必须是 created，实际 {frm!r}",
                ))
        else:
            # --- 判据 2：链式连续性 ---
            if frm != previous_to:
                problems.append((
                    f"{pointer}/from",
                    f"state_transition 链式断裂：from={frm!r} 与上一条的 to={previous_to!r} 不连续",
                ))

        # --- 判据 5 + 3：from/to 必须是合法状态名，且这条边在权威表内 ---
        if isinstance(frm, str) and isinstance(to, str):
            targets = authority.get(frm)
            if targets is None:
                problems.append((
                    f"{pointer}/from",
                    f"state_transition 的 from={frm!r} 不是合法状态",
                ))
            elif to not in targets:
                problems.append((
                    pointer,
                    f"非法状态转移 {frm} → {to}：权威表里 {frm} 的合法目标是 "
                    f"{json.dumps(targets, ensure_ascii=False)}",
                ))
        else:
            problems.append((
                pointer,
                f"state_transition 的 from/to 必须是字符串，实际 from={frm!r} to={to!r}",
            ))

        # --- 判据 4：离开 outcome_unknown 必须带 evidence_ref ---
        if frm == "outcome_unknown" and not payload.get("evidence_ref"):
            problems.append((
                f"{pointer}/evidence_ref",
                "离开 outcome_unknown 必须带 evidence_ref（外部对账证据），实际缺失",
            ))

        # 链式推进用**本条的 to**（字面值）：即便本条边非法，下一条的连续性
        # 仍然按「上一条写了什么」来判 —— 两条判据各自独立，不互相掩盖。
        if isinstance(to, str):
            previous_to = to
    return problems


# ---------------------------------------------------------------------------
# 子命令 2：--all
# ---------------------------------------------------------------------------


def list_fixtures(directory: Path) -> list:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.json") if p.is_file())


def cmd_all(_args) -> int:
    try:
        schema = load_json(SCHEMA_PATH)
    except FileNotFoundError:
        out(f"schema file not found: {rel(SCHEMA_PATH)}")
        return 1

    # 纪律④：权威转移表**必须真读**，读不到 ⇒ 直接判红（不许静默跳过）
    try:
        authority = load_authority_transitions()
    except ContractError as exc:
        out(f"contract error: {exc}")
        return 1

    valid_files = list_fixtures(VALID_DIR)
    invalid_files = list_fixtures(INVALID_DIR)

    valid_pass = 0
    for path in valid_files:
        try:
            doc = load_json(path)
        except json.JSONDecodeError as exc:
            out(f"failed {rel(path)}: JSON 解析失败 {exc}")
            continue
        # 顺序：schema → 审计链（纪律⑤）→ 转移表一致性（纪律④）→ 权限档裁决 → 副作用未知标志
        errors = validate_doc(schema, doc)
        if errors:
            for err in errors:
                out(f"failed {rel(path)} path={pointer_of(err)} ({err.message})")
            continue
        chain_errors = verify_audit_chain(doc)
        if chain_errors:
            for pointer, message in chain_errors:
                out(f"failed {rel(path)} path={pointer} ({message})")
            continue
        transition_errors = check_fixture_transitions(doc, authority)
        if transition_errors:
            for pointer, message in transition_errors:
                out(f"failed {rel(path)} path={pointer} ({message})")
            continue
        tier_errors = check_effective_tier(doc)
        if tier_errors:
            for pointer, message in tier_errors:
                out(f"failed {rel(path)} path={pointer} ({message})")
            continue
        side_effect_unknown_errors = check_side_effect_unknown(doc)
        if side_effect_unknown_errors:
            for pointer, message in side_effect_unknown_errors:
                out(f"failed {rel(path)} path={pointer} ({message})")
            continue
        replay_errors = check_replay(doc, authority)
        if replay_errors:
            for pointer, message in replay_errors:
                out(f"failed {rel(path)} path={pointer} ({message})")
            continue
        annotation_errors = check_tool_annotations(doc)
        if annotation_errors:
            for pointer, message in annotation_errors:
                out(f"failed {rel(path)} path={pointer} ({message})")
            continue
        valid_pass += 1

    invalid_rejected = 0
    for path in invalid_files:
        try:
            doc = load_json(path)
        except json.JSONDecodeError as exc:
            out(f"rejected {rel(path)} path=/ (JSON 解析失败: {exc})")
            invalid_rejected += 1
            continue
        # 顺序同上：schema 错误优先（保住 §4.2 表格钉死的期望拒绝路径），
        # 其次链校验，再次转移表一致性。
        errors = validate_doc(schema, doc)
        if errors:
            invalid_rejected += 1
            out(f"rejected {rel(path)} path={pointer_of(errors[0])}")
            continue
        chain_errors = verify_audit_chain(doc)
        if chain_errors:
            invalid_rejected += 1
            out(f"rejected {rel(path)} path={chain_errors[0][0]}")
            continue
        transition_errors = check_fixture_transitions(doc, authority)
        if transition_errors:
            invalid_rejected += 1
            out(f"rejected {rel(path)} path={transition_errors[0][0]}")
            continue
        tier_errors = check_effective_tier(doc)
        if tier_errors:
            invalid_rejected += 1
            out(f"rejected {rel(path)} path={tier_errors[0][0]}")
            continue
        side_effect_unknown_errors = check_side_effect_unknown(doc)
        if side_effect_unknown_errors:
            invalid_rejected += 1
            out(f"rejected {rel(path)} path={side_effect_unknown_errors[0][0]}")
            continue
        replay_errors = check_replay(doc, authority)
        if replay_errors:
            invalid_rejected += 1
            out(f"rejected {rel(path)} path={replay_errors[0][0]}")
            continue
        annotation_errors = check_tool_annotations(doc)
        if annotation_errors:
            invalid_rejected += 1
            out(f"rejected {rel(path)} path={annotation_errors[0][0]}")
            continue
        out(f"failed {rel(path)}: 本应被拒却通过了校验")

    out(f"valid={valid_pass}/{len(valid_files)} passed")
    out(f"invalid={invalid_rejected}/{len(invalid_files)} rejected")

    if valid_pass == len(valid_files) and invalid_rejected == len(invalid_files):
        return 0
    # fixture 不符预期 ⇒ exit 2（契约资产本身读不到 / 坏 ⇒ exit 1）
    return 2


# ---------------------------------------------------------------------------
# 子命令 6：--verify-chain
# ---------------------------------------------------------------------------


def cmd_verify_chain(_args) -> int:
    """复算所有含 run.audit 的 valid fixture 的哈希链（纪律⑤）。

    输出（逐字节稳定）：
        chain ok <相对路径> entries=<N>
        failed <相对路径> path=<RFC6901> (<原因>)
        chain=<通过数>/<总数> verified
    """
    try:
        schema = load_json(SCHEMA_PATH)
    except FileNotFoundError:
        out(f"schema file not found: {rel(SCHEMA_PATH)}")
        return 1

    total = 0
    ok = 0
    for path in list_fixtures(VALID_DIR):
        try:
            doc = load_json(path)
        except json.JSONDecodeError as exc:
            out(f"failed {rel(path)}: JSON 解析失败 {exc}")
            continue
        audit = (doc.get("run") or {}).get("audit")
        if not isinstance(audit, list) or not audit:
            continue  # 不含 audit 的 fixture 不参与链校验
        total += 1
        # 先看 schema（链字段的形状得合法才能复算），再复算链
        errors = validate_doc(schema, doc)
        if errors:
            out(f"failed {rel(path)} path={pointer_of(errors[0])} ({errors[0].message})")
            continue
        chain_errors = verify_audit_chain(doc)
        if chain_errors:
            for pointer, message in chain_errors:
                out(f"failed {rel(path)} path={pointer} ({message})")
            continue
        ok += 1
        out(f"chain ok {rel(path)} entries={len(audit)}")

    out(f"chain={ok}/{total} verified")
    return 0 if ok == total else 2


# ---------------------------------------------------------------------------
# 子命令 6：--replay（v0.4）
# ---------------------------------------------------------------------------


def cmd_replay(_args) -> int:
    """重放所有 valid fixture 的状态转移序列（v0.4 语义层判据）。

    输出（逐字节稳定）：
        replay ok <相对路径> transitions=<N>
        failed <相对路径> path=<RFC6901> (<原因>)
        replay=<通过数>/<总数> verified

    判据内容见 `check_replay()`：链式连续 + 边在权威表内 + 离开未知态带 evidence_ref。
    """
    try:
        schema = load_json(SCHEMA_PATH)
    except FileNotFoundError:
        out(f"schema file not found: {rel(SCHEMA_PATH)}")
        return 1

    # 权威表**必须真读**，读不到 ⇒ 直接判红（不许静默跳过）
    try:
        authority = load_authority_transitions()
    except ContractError as exc:
        out(f"contract error: {exc}")
        return 1

    total = 0
    ok = 0
    for path in list_fixtures(VALID_DIR):
        try:
            doc = load_json(path)
        except json.JSONDecodeError as exc:
            out(f"failed {rel(path)}: JSON 解析失败 {exc}")
            continue
        audit = (doc.get("run") or {}).get("audit")
        if not isinstance(audit, list) or not audit:
            continue  # 不含 audit 的 fixture 不参与重放
        total += 1
        # 先看 schema（条目形状得合法才能重放），再重放
        errors = validate_doc(schema, doc)
        if errors:
            out(f"failed {rel(path)} path={pointer_of(errors[0])} ({errors[0].message})")
            continue
        replay_errors = check_replay(doc, authority)
        if replay_errors:
            for pointer, message in replay_errors:
                out(f"failed {rel(path)} path={pointer} ({message})")
            continue
        ok += 1
        out(f"replay ok {rel(path)} transitions={count_transitions(doc)}")

    out(f"replay={ok}/{total} verified")
    return 0 if ok == total else 2


def count_transitions(doc) -> int:
    """文档里 `type=="state_transition"` 的审计条目数。"""
    audit = (doc.get("run") or {}).get("audit")
    if not isinstance(audit, list):
        return 0
    return sum(1 for entry in audit
               if isinstance(entry, dict) and entry.get("type") == STATE_TRANSITION_TYPE)


# ---------------------------------------------------------------------------
# 子命令 3：--coverage
# ---------------------------------------------------------------------------


def collect_coverage(valid_files: list) -> tuple:
    """扫描 valid/，收集出现过的枚举取值与边界命中情况。"""
    enums = {
        "approval_status": set(),
        "capability_tier": set(),
        "clock": set(),
        "framework": set(),
        "retry_profile": set(),
        "run_status": set(),
        "side_effect": set(),
        "effective_tier": set(),
    }
    boundaries = {
        "all_budgets_set": False,
        "approval_expired": False,
        "args_ref_used": False,
        "attempt_ge_2": False,
        "float_in_args": False,
        "multi_step": False,
        "outcome_unknown": False,
        "reservation_settled": False,
        "scope_is_null": False,
        "tier_downgraded": False,
    }

    for path in valid_files:
        doc = load_json(path)
        spec = doc.get("spec") or {}
        run = doc.get("run") or {}
        # v0.3：step 是**数组**
        steps = doc.get("step") or []
        if not isinstance(steps, list):
            steps = [steps]
        policy = run.get("policy") or {}
        retry = policy.get("retry_profile") or {}
        budget = run.get("budget") or {}
        policy_tier = policy.get("capability_tier")

        if len(steps) >= 2:
            boundaries["multi_step"] = True

        for step in steps:
            if not isinstance(step, dict):
                continue
            approval = step.get("approval") or {}
            granted = step.get("granted") or {}
            grant_request = step.get("grant_request") or {}

            enums["framework"].add(spec.get("framework"))
            enums["capability_tier"].add(policy_tier)
            enums["run_status"].add(run.get("status"))
            enums["retry_profile"].add(retry.get("name"))
            enums["approval_status"].add(approval.get("status"))
            enums["side_effect"].add(step.get("side_effect"))
            enums["effective_tier"].add(step.get("effective_tier"))
            enums["clock"].add(spec.get("clock"))
            enums["clock"].add(run.get("clock"))
            enums["clock"].add(granted.get("clock"))

            # 边界：去重范围为 null
            if "dedupe_scope" in step and step["dedupe_scope"] is None:
                boundaries["scope_is_null"] = True
            if "dedupe_scope" in grant_request and grant_request["dedupe_scope"] is None:
                boundaries["scope_is_null"] = True
            attempt = step.get("attempt")
            if isinstance(attempt, int) and attempt >= 2:
                boundaries["attempt_ge_2"] = True
            if "args_ref" in step:
                boundaries["args_ref_used"] = True
            if approval.get("status") == "expired":
                boundaries["approval_expired"] = True
            # 边界：args 里出现浮点（v0.3 放开浮点 ⇒ 须有样例证明能表达）
            if _contains_float(step.get("args")):
                boundaries["float_in_args"] = True
            # 边界：effective_tier 被工具档压低（< policy 档）
            tier = step.get("effective_tier")
            if tier in TIER_ORDER and policy_tier in TIER_ORDER:
                if TIER_ORDER[tier] < TIER_ORDER[policy_tier]:
                    boundaries["tier_downgraded"] = True

        for tool in run.get("tools") or []:
            enums["side_effect"].add(tool.get("side_effect"))

        if run.get("status") == "outcome_unknown":
            boundaries["outcome_unknown"] = True
        for reservation in run.get("reservations") or []:
            if isinstance(reservation, dict) and reservation.get("state") == "settled":
                boundaries["reservation_settled"] = True
        quota_keys = ("max_steps", "max_tool_calls", "max_tokens", "max_cost_usd")
        if all(isinstance(budget.get(k), int) and budget.get(k) > 0 for k in quota_keys):
            boundaries["all_budgets_set"] = True

    return enums, boundaries


def _contains_float(node) -> bool:
    """载荷里是否出现浮点数（v0.3 允许 ⇒ 覆盖检查须确认有样例）。"""
    if isinstance(node, bool):
        return False
    if isinstance(node, float):
        return True
    if isinstance(node, dict):
        return any(_contains_float(value) for value in node.values())
    if isinstance(node, list):
        return any(_contains_float(item) for item in node)
    return False


def cmd_coverage(_args) -> int:
    valid_files = list_fixtures(VALID_DIR)
    if not valid_files:
        out('{"boundaries":{},"enums":{},"missing":["no valid fixtures"]}')
        return 3

    enums, boundaries = collect_coverage(valid_files)

    expected = {
        "approval_status": APPROVAL_STATUS_VALUES,
        "capability_tier": CAPABILITY_TIER_VALUES,
        "clock": CLOCK_VALUES,
        "effective_tier": CAPABILITY_TIER_VALUES,
        "retry_profile": RETRY_PROFILE_VALUES,
        "run_status": RUN_STATUS_VALUES,
        "side_effect": SIDE_EFFECT_VALUES,
    }
    # v0.3：`spec.framework` 是**自由字符串**（不再有枚举）⇒ 不再要求「覆盖某个框架集合」；
    # 但仍统计出现过哪些值（enums.framework），供人看覆盖面。

    missing = []
    for key, values in expected.items():
        for value in values:
            if value not in enums[key]:
                missing.append(f"enum:{key}:{value}")
    for key, hit in boundaries.items():
        if not hit:
            missing.append(f"boundary:{key}")

    result = {
        "boundaries": {k: boundaries[k] for k in sorted(boundaries)},
        "enums": {k: sorted(v for v in enums[k] if v is not None) for k in sorted(enums)},
        "missing": sorted(missing),
    }
    out(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))

    if missing:
        return 3
    return 0


# ---------------------------------------------------------------------------
# 子命令 4：--fields
# ---------------------------------------------------------------------------


def collect_required_paths(schema: dict, node: dict, prefix: str, seen_refs=None) -> list:
    """收集某 section 下所有必填字段路径（含 oneOf 分支的必填项）。

    v0.3：`step` 顶层已是**数组** ⇒ 本函数遇到数组节点时**下钻到 items**，
    列出数组元素内部的必填字段（路径前缀不变，仍是 `step.`）。
    """
    if seen_refs is None:
        seen_refs = set()
    paths = []
    # v0.3：节点本身是数组 ⇒ 下钻到 items（元素级必填项）
    if node.get("type") == "array":
        items = node.get("items") or {}
        child = items
        if "$ref" in child:
            ref = child["$ref"]
            if ref in seen_refs:
                return paths
            seen_refs = seen_refs | {ref}
            child = resolve_ref(schema, ref)
        if isinstance(child, dict) and (is_object_schema(child) or "properties" in child):
            paths.extend(collect_required_paths(schema, child, prefix, seen_refs))
        return paths
    required = list(node.get("required") or [])
    for name in required:
        path = f"{prefix}{name}"
        prop = (node.get("properties") or {}).get(name)
        child = prop
        if isinstance(child, dict) and "$ref" in child:
            ref = child["$ref"]
            if ref in seen_refs:
                paths.append(path)
                continue
            seen_refs = seen_refs | {ref}
            child = resolve_ref(schema, ref)
        # 数组元素内的必填项不算「本 section 的必填路径」——只记数组本身
        if isinstance(child, dict) and child.get("type") == "array":
            paths.append(path)
        elif isinstance(child, dict) and (is_object_schema(child) or "properties" in child):
            paths.extend(collect_required_paths(schema, child, path + ".", seen_refs))
        else:
            paths.append(path)

    # oneOf / anyOf 分支里的必填项（如 step.args / step.args_ref 二选一）
    for branch_key in ("oneOf", "anyOf"):
        for branch in node.get(branch_key) or []:
            if not isinstance(branch, dict):
                continue
            for name in branch.get("required") or []:
                path = f"{prefix}{name}"
                if path not in paths:
                    paths.append(path)
    return paths


def cmd_fields(args) -> int:
    """列出必填字段路径。

    `--fields --count`：额外输出**实际必填字段总数**（评审 (18)：
    README 不许再写死「18 fields」这种数字 ⇒ 引用命令即可）。
    """
    try:
        schema = load_json(SCHEMA_PATH)
    except FileNotFoundError:
        out(f"schema file not found: {rel(SCHEMA_PATH)}")
        return 1

    result = {}
    for section in ("run", "spec", "step"):
        node = schema["properties"][section]
        if "$ref" in node:
            node = resolve_ref(schema, node["$ref"])
        paths = collect_required_paths(schema, node, f"{section}.")
        result[section] = sorted(set(paths))

    # 契约级元数据：状态转移表挂在文档根 ⇒ 列在 "contract" 键下（不混进 run）
    transition_node = (schema.get("properties") or {}).get("x-status-transitions") or {}
    if "$ref" in transition_node:
        transition_node = resolve_ref(schema, transition_node["$ref"])
    result["contract"] = sorted(
        f"x-status-transitions.{state}" for state in (transition_node.get("properties") or {})
    )

    if getattr(args, "count", False):
        # 自动导出：总数 = 四个 section 的必填路径之和（去重后）
        total = sum(len(paths) for paths in result.values())
        out(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        out(f"count={total}")
        return 0

    out(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# 子命令 5：--digest
# ---------------------------------------------------------------------------


def canonicalize(value, pointer: str = "$"):
    """canonical JSON 规范化（README §args_digest 的算法实现）。

    * **v0.3**：允许浮点（LLM 参数如 `temperature=0.7` 必须能表达）；
      浮点按 **ECMAScript Number::toString** 序列化（与 JCS 同一口径，见 `_es_number`）；
    * 对象键按**码位序**排序（键先做 NFC 归一化再排序）—— 这是 args_digest 的口径，
      **与纪律⑤ 的 JCS 键序（UTF-16 码元序）不是同一件事**，别混用；
    * 数组保序；
    * 值为 null 的键剔除；
    * 字符串做 Unicode NFC。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # 金额类字段另有 schema + scan_floats 兜底（禁浮点）；args 里的浮点放行
        return value
    if value is None:
        return None
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [canonicalize(item, f"{pointer}/{index}") for index, item in enumerate(value)]
    if isinstance(value, dict):
        result = {}
        for raw_key in sorted(value.keys(), key=lambda k: unicodedata.normalize("NFC", k)):
            key = unicodedata.normalize("NFC", raw_key)
            child = value[raw_key]
            if child is None:
                continue  # null 值的键剔除（未传 ≠ 传 null）
            result[key] = canonicalize(child, f"{pointer}/{raw_key}")
        return result
    raise DigestError(f"{pointer}: 不支持的类型 {type(value).__name__}")


def canonical_json(value) -> str:
    """canonical JSON 序列化（args_digest 口径）。

    * 键排序：**码位序**（args_digest 口径，见 README §3 第 2 条）——
      注意这与纪律⑤ 的 JCS **UTF-16 码元序**不同（两个算法、两种排序，别混用）；
    * 数字：整数原样；浮点按 **ECMAScript Number::toString**（`_es_number`）；
    * 紧凑分隔符、非 ASCII 不转义。
    """
    out: list = []
    _canonical_emit(value, out)
    return "".join(out)


def _canonical_emit(value, out: list) -> None:
    """canonical JSON 序列化器（键按码位序 + ES 数字）。"""
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        out.append(str(value))
    elif isinstance(value, float):
        out.append(_es_number(value))
    elif isinstance(value, str):
        out.append(json.encoder.encode_basestring(value))
    elif isinstance(value, list):
        out.append("[")
        for index, item in enumerate(value):
            if index:
                out.append(",")
            _canonical_emit(item, out)
        out.append("]")
    elif isinstance(value, dict):
        out.append("{")
        for index, key in enumerate(sorted(value.keys(), key=lambda k: unicodedata.normalize("NFC", k))):
            if index:
                out.append(",")
            out.append(json.encoder.encode_basestring(key))
            out.append(":")
            _canonical_emit(value[key], out)
        out.append("}")
    else:
        raise DigestError(f"不支持的类型 {type(value).__name__}")


def cmd_digest(args) -> int:
    target = Path(args.digest)
    candidates = [target] if target.is_absolute() else [ROOT / target, Path.cwd() / target]
    path = None
    for candidate in candidates:
        if candidate.is_file():
            path = candidate
            break
    if path is None:
        out(f"digest error: fixture 不存在：{args.digest}")
        return 2

    try:
        doc = load_json(path)
    except json.JSONDecodeError as exc:
        out(f"digest error: {rel(path)} JSON 解析失败：{exc}")
        return 2

    try:
        schema = load_json(SCHEMA_PATH)
    except FileNotFoundError:
        out(f"digest error: schema 不存在：{rel(SCHEMA_PATH)}")
        return 2

    errors = validate_doc(schema, doc)
    if errors:
        out(f"digest error: {rel(path)} 不符合契约：{pointer_of(errors[0])} ({errors[0].message})")
        return 2

    # v0.3：step 是**数组** ⇒ digest 取**第一个**带 args/args_ref 的 step
    # （label 保持 "step.args" 形态 ⇒ 双端错误消息逐字节一致，CONFORMANCE §2.1）
    steps = doc.get("step") or []
    if not isinstance(steps, list):
        steps = [steps]
    source = None
    label = None
    for step in steps:
        if not isinstance(step, dict):
            continue
        if "args" in step:
            source = step["args"]
            label = "step.args"
            break
        if "args_ref" in step:
            source = step["args_ref"]
            label = "step.args_ref"
            break
    if source is None:
        out(f"digest error: {rel(path)} 既无 step.args 也无 step.args_ref")
        return 2

    try:
        canonical = canonical_json(canonicalize(source, label))
    except DigestError as exc:
        out(f"digest error: {exc}")
        return 2

    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    out(f"canonical: {canonical}")
    out(f"sha256: {digest}")
    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="contract_check.py",
        description="Agent Run 语义契约 v0.3.0 校验器（Python 侧）",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--schema", action="store_true", help="校验 schema 自身 + 三条纪律 + 五条断言")
    group.add_argument("--all", action="store_true", help="跑全部 fixtures（valid 全过 / invalid 全拒）")
    group.add_argument("--coverage", action="store_true", help="报枚举与边界覆盖")
    group.add_argument("--fields", action="store_true", help="列出必填字段路径")
    group.add_argument("--digest", metavar="FIXTURE", help="算 canonical JSON 与 sha256")
    group.add_argument("--verify-chain", action="store_true",
                       help="复算所有含 run.audit 的 valid fixture 的审计链（纪律⑤）")
    group.add_argument("--replay", action="store_true",
                       help="重放 valid fixture 的状态转移序列（v0.4 语义层判据）")
    # --count 是 --fields 的**修饰符**，不是独立命令 ⇒ 放在互斥组之外
    parser.add_argument("--count", action="store_true",
                        help="配合 --fields：额外输出必填字段总数（README 引用它，不写死数字）")

    args = parser.parse_args(argv)
    try:
        if args.schema:
            return cmd_schema(args)
        if args.all:
            return cmd_all(args)
        if args.coverage:
            return cmd_coverage(args)
        if args.fields:
            return cmd_fields(args)
        if args.digest:
            return cmd_digest(args)
        if args.verify_chain:
            return cmd_verify_chain(args)
        if args.replay:
            return cmd_replay(args)
    except ContractError as exc:
        out(f"contract error: {exc}")
        return 1
    except DigestError as exc:
        out(f"digest error: {exc}")
        return 2
    parser.error("no command")
    return 1


if __name__ == "__main__":
    sys.exit(main())
