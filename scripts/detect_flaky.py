"""随机顺序重复跑核心子集，检测 flaky 测试。

用法：
    python scripts/detect_flaky.py
    python scripts/detect_flaky.py --scope unit --runs 3
    python scripts/detect_flaky.py --scope integration --runs 3
    python scripts/detect_flaky.py --scope all --runs 3
    python scripts/detect_flaky.py --path tests/unit/ --runs 3

退出码：
    0: 未检测到 flaky（所有运行结果一致）
    1: 检测到 flaky（运行结果不一致）
    2: 路径不存在或运行出错
"""

import argparse
import subprocess
import sys

_SCOPE_PATHS = {
    "unit": "tests/unit/",
    "integration": "tests/integration/",
    "all": "tests/unit/ tests/integration/",
}


def main():
    parser = argparse.ArgumentParser(description="检测 flaky 测试")
    parser.add_argument(
        "--scope",
        choices=["unit", "integration", "all"],
        default="all",
        help="测试范围（默认 all，覆盖 unit 和 integration）",
    )
    parser.add_argument("--path", default=None, help="测试路径（覆盖 --scope，例如 tests/unit/）")
    parser.add_argument("--runs", type=int, default=3, help="运行次数（默认 3）")
    args = parser.parse_args()

    test_path = args.path if args.path else _SCOPE_PATHS[args.scope]

    results = []
    for i in range(args.runs):
        print(f"运行 {i + 1}/{args.runs}...")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", *test_path.split(), "-q", "--tb=no", "-p", "no:randomly"],
            capture_output=True,
            text=True,
        )
        results.append(result.returncode)
        print(f"  退出码: {result.returncode}")

    if len(set(results)) > 1:
        print(f"\n✗ 检测到 flaky 测试：{args.runs} 次运行结果不一致 {results}")
        return 1
    else:
        print(f"\n✓ 未检测到 flaky 测试（{args.runs} 次运行结果一致）")
        return 0


if __name__ == "__main__":
    sys.exit(main())
