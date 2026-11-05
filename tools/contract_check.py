#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent Run 语义契约 v0.1 —— Python 侧校验器 / canonical digest 实现。

用途
----
1. 校验契约自身合法性，并断言三条纪律（additionalProperties / 整数金额 / 状态转移表一致）。
2. 跑全部 fixtures：valid 全过、invalid 全拒，并报 RFC 6901 路径。
3. 报枚举与边界覆盖情况。
4. 列出契约的必填字段路径。
5. 算 args_digest（canonical JSON + sha256），供 Java 侧逐字节比对。

设计纪律（双端比对的前提）
--------------------------
* 输出必须**稳定**：无时间戳、无绝对路径、键排序、LF 结尾 —— 双端比的就是字符串。
* 金额一律整数 micro-USD，禁浮点；args 禁浮点（见 README §args_digest）。
* 遇到浮点直接 exit 2，**不许**把它转义成 \\uXXXX 充数（那会让两端 canonical 不一致）。

用法
----
    python tools/contract_check.py --schema
    python tools/contract_check.py --all
    python tools/contract_check.py --coverage
    python tools/contract_check.py --fields
    python tools/contract_check.py --digest contract/fixtures/valid/step_full.json

退出码
------
    0  通过
    1  校验失败（schema 纪律不过 / fixtures 不符预期）
    2  digest 相关错误（浮点、路径不存在、fixture 非法）
    3  覆盖缺口（--coverage 有 missing）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量与路径（全部相对仓库根，绝不出现本机绝对路径）
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_DIR = ROOT / "contract"
SCHEMA_PATH = CONTRACT_DIR / "run_semantics.schema.json"
VALID_DIR = CONTRACT_DIR / "fixtures" / "valid"
INVALID_DIR = CONTRACT_DIR / "fixtures" / "invalid"

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
    """扫描整份文档里的浮点数（禁浮点纪律的兜底检查）。

    金额字段（`*_usd`）与 args 一旦出现浮点即视为违规 —— jsonschema 对 `1.0`
    这类值的判定依赖具体实现，这里显式兜底，保证双端行为一致。
    """
    if found is None:
        found = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{pointer}/{key}"
            if key.endswith("_usd") and isinstance(value, float):
                found.append(FakeError(child, f"{value} 是浮点数，金额必须是整数 micro-USD"))
            scan_floats(value, child, found)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            scan_floats(value, f"{pointer}/{index}", found)
    elif isinstance(node, float):
        # 其它位置的浮点（args 里最常见）：单独定位一次
        found.append(FakeError(pointer, f"{node} 是浮点数，契约禁浮点"))
    return found


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
    """三条纪律 + 两条形状断言：
    ① 每个 object 都有 additionalProperties: false（显式标注 x-free-form 的自由载荷容器豁免）；
    ② 所有 `*_usd` 字段 type 为 integer；
    ③ x-status-transitions 的 key/value 都在 run.status 枚举内（转移表在**文档根**）；
    ④ $defs/spec 不含 capability_tier（自造轴已删）；
    ⑤ $defs/policy 含 capability_tier 且 enum 恰为 ["read","write","destructive"]。
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

    valid_files = list_fixtures(VALID_DIR)
    invalid_files = list_fixtures(INVALID_DIR)

    valid_pass = 0
    for path in valid_files:
        try:
            doc = load_json(path)
        except json.JSONDecodeError as exc:
            out(f"failed {rel(path)}: JSON 解析失败 {exc}")
            continue
        errors = validate_doc(schema, doc)
        if errors:
            for err in errors:
                out(f"failed {rel(path)} path={pointer_of(err)} ({err.message})")
        else:
            valid_pass += 1

    invalid_rejected = 0
    for path in invalid_files:
        try:
            doc = load_json(path)
        except json.JSONDecodeError as exc:
            out(f"rejected {rel(path)} path=/ (JSON 解析失败: {exc})")
            invalid_rejected += 1
            continue
        errors = validate_doc(schema, doc)
        if not errors:
            out(f"failed {rel(path)}: 本应被拒却通过了校验")
            continue
        invalid_rejected += 1
        out(f"rejected {rel(path)} path={pointer_of(errors[0])}")

    out(f"valid={valid_pass}/{len(valid_files)} passed")
    out(f"invalid={invalid_rejected}/{len(invalid_files)} rejected")

    if valid_pass == len(valid_files) and invalid_rejected == len(invalid_files):
        return 0
    return 1


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
    }
    boundaries = {
        "all_budgets_set": False,
        "approval_expired": False,
        "args_ref_used": False,
        "attempt_ge_2": False,
        "outcome_unknown": False,
        "scope_is_null": False,
    }

    for path in valid_files:
        doc = load_json(path)
        spec = doc.get("spec") or {}
        run = doc.get("run") or {}
        step = doc.get("step") or {}
        policy = run.get("policy") or {}
        retry = policy.get("retry_profile") or {}
        approval = step.get("approval") or {}
        budget = run.get("budget") or {}
        granted = step.get("granted") or {}
        grant_request = step.get("grant_request") or {}

        enums["framework"].add(spec.get("framework"))
        enums["capability_tier"].add(policy.get("capability_tier"))
        enums["run_status"].add(run.get("status"))
        enums["retry_profile"].add(retry.get("name"))
        enums["approval_status"].add(approval.get("status"))
        enums["side_effect"].add(step.get("side_effect"))
        for tool in run.get("tools") or []:
            enums["side_effect"].add(tool.get("side_effect"))
        enums["clock"].add(spec.get("clock"))
        enums["clock"].add(run.get("clock"))
        enums["clock"].add(granted.get("clock"))

        # 边界
        if "idempotency_scope" in step and step["idempotency_scope"] is None:
            boundaries["scope_is_null"] = True
        if "idempotency_scope" in grant_request and grant_request["idempotency_scope"] is None:
            boundaries["scope_is_null"] = True
        attempt = step.get("attempt")
        if isinstance(attempt, int) and attempt >= 2:
            boundaries["attempt_ge_2"] = True
        if "args_ref" in step:
            boundaries["args_ref_used"] = True
        if run.get("status") == "outcome_unknown":
            boundaries["outcome_unknown"] = True
        if approval.get("status") == "expired":
            boundaries["approval_expired"] = True
        quota_keys = ("max_steps", "max_tool_calls", "max_tokens", "max_cost_usd")
        if all(isinstance(budget.get(k), int) and budget.get(k) > 0 for k in quota_keys):
            boundaries["all_budgets_set"] = True

    return enums, boundaries


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
        "framework": FRAMEWORK_VALUES,
        "retry_profile": RETRY_PROFILE_VALUES,
        "run_status": RUN_STATUS_VALUES,
        "side_effect": SIDE_EFFECT_VALUES,
    }

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
    """收集某 section 下所有必填字段路径（含 oneOf 分支的必填项）。"""
    if seen_refs is None:
        seen_refs = set()
    paths = []
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


def cmd_fields(_args) -> int:
    try:
        schema = load_json(SCHEMA_PATH)
    except FileNotFoundError:
        out(f"schema file not found: {rel(SCHEMA_PATH)}")
        return 1

    result = {}
    for section in ("run", "spec", "step"):
        ref = schema["properties"][section]["$ref"]
        node = resolve_ref(schema, ref)
        paths = collect_required_paths(schema, node, f"{section}.")
        result[section] = sorted(set(paths))

    # 契约级元数据：状态转移表挂在文档根 ⇒ 列在 "contract" 键下（不混进 run）
    transition_node = (schema.get("properties") or {}).get("x-status-transitions") or {}
    if "$ref" in transition_node:
        transition_node = resolve_ref(schema, transition_node["$ref"])
    result["contract"] = sorted(
        f"x-status-transitions.{state}" for state in (transition_node.get("properties") or {})
    )

    out(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# 子命令 5：--digest
# ---------------------------------------------------------------------------


def canonicalize(value, pointer: str = "$"):
    """canonical JSON 规范化（README §args_digest 的算法实现）。

    * 遇浮点直接抛 DigestError；
    * 对象键按码位序排序（键先做 NFC 归一化再排序）；
    * 数组保序；
    * 值为 null 的键剔除；
    * 字符串做 Unicode NFC。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        raise DigestError(f"{pointer}: 浮点数不允许进入 canonical（禁浮点纪律）")
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
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


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

    step = doc.get("step") or {}
    if "args" in step:
        source = step["args"]
        label = "step.args"
    elif "args_ref" in step:
        source = step["args_ref"]
        label = "step.args_ref"
    else:
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
        description="Agent Run 语义契约 v0.1 校验器（Python 侧）",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--schema", action="store_true", help="校验 schema 自身 + 三条纪律")
    group.add_argument("--all", action="store_true", help="跑全部 fixtures（valid 全过 / invalid 全拒）")
    group.add_argument("--coverage", action="store_true", help="报枚举与边界覆盖")
    group.add_argument("--fields", action="store_true", help="列出必填字段路径")
    group.add_argument("--digest", metavar="FIXTURE", help="算 canonical JSON 与 sha256")

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
