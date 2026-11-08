"""对拍 golden：用**本仓 Python 实现**跑同样命令，与 `tools/golden/`（由 Java 实现生成）逐字节比较。

用法：
    python tools/check_golden.py            # 全量对拍（7 命令 + 26 digest + 退出码）
    python tools/check_golden.py --quiet    # 只打结论
    python tools/check_golden.py --list     # 只列会对拍哪些项

为什么要有它（评审硬伤 3）："两个实现逐字节一致"原先只是自述；本脚本把 Java 侧输出
（golden）与本仓 Python 侧输出**逐字节**比较，任何外部 reviewer clone 后一条命令即可复核。
退出码：0 = 全部一致；1 = 有不一致（打印第一处差异的行号）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GOLDEN = REPO / "tools" / "golden"
CHECKER = REPO / "tools" / "contract_check.py"

#: 命令 → golden 文件名（顺序即输出顺序）
COMMANDS: tuple[tuple[str, str], ...] = (
    ("--all", "all.txt"),
    ("--schema", "schema.txt"),
    ("--coverage", "coverage.txt"),
    ("--fields", "fields.txt"),
    ("--fields --count", "fields_count.txt"),
    ("--verify-chain", "verify_chain.txt"),
    ("--replay", "replay.txt"),
)


def _run(args: list[str]) -> tuple[bytes, int]:
    """跑 Python 校验器，返回 (stdout 字节, 退出码)。"""
    proc = subprocess.run(
        [sys.executable, str(CHECKER), *args],
        cwd=REPO,
        capture_output=True,
    )
    return proc.stdout, proc.returncode


def _first_diff(expected: bytes, actual: bytes) -> str:
    """给出第一处差异的人类可读说明。"""
    if expected == actual:
        return ""
    exp_lines = expected.split(b"\n")
    act_lines = actual.split(b"\n")
    for idx, (exp, act) in enumerate(zip(exp_lines, act_lines), 1):
        if exp != act:
            return (
                f"第 {idx} 行不同：\n"
                f"    期望(golden/java): {exp[:160]!r}\n"
                f"    实际(python)     : {act[:160]!r}"
            )
    return f"行数不同：期望 {len(exp_lines)} 行，实际 {len(act_lines)} 行"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Python 实现 vs Java golden 逐字节对拍")
    parser.add_argument("--quiet", action="store_true", help="只打结论")
    parser.add_argument("--list", action="store_true", help="只列对拍项")
    args = parser.parse_args(argv)

    if not GOLDEN.is_dir():
        print(f"缺少 golden 目录：{GOLDEN}", file=sys.stderr)
        return 1
    if not CHECKER.is_file():
        print(f"缺少校验器：{CHECKER}", file=sys.stderr)
        return 1

    digests = sorted((GOLDEN / "digests").glob("*.json"))
    if args.list:
        print(f"对拍项：{len(COMMANDS)} 命令 + {len(digests)} digest + 退出码")
        for cmd, fname in COMMANDS:
            print(f"  {cmd:<18} → {fname}")
        return 0

    same = diff = 0
    failures: list[str] = []

    # 1) 命令输出
    exit_expected: dict[str, int] = {}
    exit_file = GOLDEN / "exit_codes.json"
    if exit_file.is_file():
        exit_expected = json.loads(exit_file.read_text(encoding="utf-8"))

    for cmd, fname in COMMANDS:
        golden_path = GOLDEN / fname
        if not golden_path.is_file():
            failures.append(f"缺少 golden 文件：{fname}")
            diff += 1
            continue
        expected = golden_path.read_bytes()
        actual, code = _run(cmd.split())
        exp_code = exit_expected.get(cmd)
        ok_out = expected == actual
        ok_code = exp_code is None or exp_code == code
        if ok_out and ok_code:
            same += 1
        else:
            diff += 1
            detail = _first_diff(expected, actual) if not ok_out else ""
            failures.append(
                f"[命令] {cmd}: 输出一致={ok_out} 退出码一致={ok_code}"
                + (f"\n    {detail}" if detail else "")
            )

    # 2) 每份 fixture 的 digest
    for golden_path in digests:
        name = golden_path.stem
        candidates = list((REPO / "contract" / "fixtures").rglob(name + ".json"))
        if not candidates:
            failures.append(f"[digest] fixture 不存在：{name}.json")
            diff += 1
            continue
        expected = golden_path.read_bytes()
        actual, _ = _run(["--digest", str(candidates[0])])
        if expected == actual:
            same += 1
        else:
            diff += 1
            failures.append(f"[digest] {name}:\n    {_first_diff(expected, actual)}")

    if not args.quiet:
        print(f"golden 对拍：SAME={same} DIFF={diff}")
    if failures:
        print("\n不一致项：")
        for item in failures:
            print(f"  {item}")
        return 1
    print(f"OK: {same} 项逐字节一致（Python 实现 vs Java golden）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
