"""契约版本一致性检查（A2 演进纪律之一）。

校验四处版本必须一致，且 CHANGELOG 必须有对应条目：
1. `run_semantics.schema.json` 的 `$id` 版本段
2. `schema.title` 里的 `vX.Y.Z`
3. `spec.version.enum` 必须包含该版本
4. `status-transitions.json` 的 `version`
5. `CHANGELOG.md` 必须存在 `## vX.Y.Z` 小节

用法：python tools/check_versions.py
退出码：0 = 一致；1 = 不一致（打印全部问题）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMA = REPO / "contract" / "run_semantics.schema.json"
TRANSITIONS = REPO / "contract" / "status-transitions.json"
CHANGELOG = REPO / "CHANGELOG.md"
VERSION_RE = re.compile(r"v(\d+\.\d+\.\d+)")


def main() -> int:
    problems: list[str] = []

    try:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        transitions = json.loads(TRANSITIONS.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"读不到契约文件：{exc}", file=sys.stderr)
        return 1

    title = str(schema.get("title") or "")
    match = VERSION_RE.search(title)
    if not match:
        problems.append(f"schema.title 里没有 vX.Y.Z：{title!r}")
        version = ""
    else:
        version = match.group(1)

    schema_id = str(schema.get("$id") or "")
    if version and version not in schema_id:
        problems.append(f"$id 不含 title 版本 {version}：{schema_id}")

    enum = (
        schema.get("$defs", {})
        .get("spec", {})
        .get("properties", {})
        .get("version", {})
        .get("enum", [])
    )
    if version and version not in enum:
        problems.append(f"spec.version.enum 不含 {version}：{enum}")

    st_version = str(transitions.get("version") or "")
    if version and st_version != version:
        problems.append(f"status-transitions.version={st_version!r} 与 title 版本 {version} 不一致")

    try:
        changelog = CHANGELOG.read_text(encoding="utf-8")
    except OSError:
        problems.append("缺少 CHANGELOG.md")
        changelog = ""
    if version and f"## v{version}" not in changelog:
        problems.append(f"CHANGELOG.md 缺少 `## v{version}` 小节")

    if problems:
        print("版本一致性检查失败：")
        for item in problems:
            print(f"  - {item}")
        return 1

    print(
        f"OK: 版本 {version} 四处一致（$id / title / spec.version.enum / status-transitions）"
        f"，CHANGELOG 有条目"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
