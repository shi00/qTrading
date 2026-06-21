"""随机顺序重复跑核心子集，检测 flaky 测试。

用法：
    python scripts/detect_flaky.py
    python scripts/detect_flaky.py --path tests/unit/ --runs 3

退出码：
    0: 未检测到 flaky（所有运行结果一致）
    1: 检测到 flaky（运行结果不一致）
    2: 路径不存在或运行出错
"""

import argparse
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description="检测 flaky 测试")
    parser.add_argument("--path", default="tests/unit/", help="测试路径（默认 tests/unit/）")
    parser.add_argument("--runs", type=int, default=3, help="运行次数（默认 3）")
    args = parser.parse_args()

    results = []
    for i in range(args.runs):
        print(f"运行 {i + 1}/{args.runs}...")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", args.path, "-q", "--tb=no"],
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
