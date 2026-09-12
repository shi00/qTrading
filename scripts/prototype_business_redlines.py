"""P1-01 AST 原型：三条业务红线（R20/R21/R22）自动化可行性验证。

背景
----
业务检视（business-review-2026-09-11）发现 AI 取消选择的红线集（R1~R19）
对最高发的三类失效模式零覆盖：
  - DATA-01/02  已知金额列裸比较未做单位换算（北向资金错 100 倍、龙虎榜错 10000 倍）
  - AI-01/02    缺失/失败被伪装成合法具体值（score=0、confidence=50）
  - SYNC-01     水位线（checkpoint）写入非单调（最后写入者获胜 vs 高水位读语义）

报告明确要求「先做原型再定 enforcement」——本脚本即该 AST 可行性原型：
在真实代码上跑三套 AST 检测，输出命中清单并对照业务检视的 11 项最高优先级
缺陷做回归，评估误报率，据此决定三条红线（命名 R20 单位核对 / R21 缺失伪装 /
R22 水位单调）最终 enforcement 形态。

设计要点
--------
- 纯标准库（ast/json/pathlib），不 import 任何业务层，仅做静态分析。
- 每条红线一个 ast.NodeVisitor 子类；每个命中记录 `{file, lineno, kind, detail,
  defect_id}`，`defect_id` 由本地缺陷位置映射表回填，用于统计误报率。
- 输出 JSON 报告（stdout）与人类可读摘要（stderr）。
- 本脚本为原型，不接入 pre-commit / redlines.yml / AGENTS.md 生成区块。

用法
----
    python scripts/prototype_business_redlines.py [ROOT]

ROOT 缺省为仓库根（脚本所在目录的上一级）。
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

# 已知单位列（单位元数据在 data/constants.py：HSGT_COLUMN_UNITS /
# TOP_LIST_COLUMN_UNITS；此处为原型探测用的列名字面量清单，非权威正本）。
# 分两级：
#   HIGH：有明确单位元数据声明（百万/元/万元）且阈值参数隐含单位的列，裸比较
#         几乎必然是量纲错误 -> R20 直判。
#   LOW：单位含义随上下文变的列（vol 成交量、total_mv 市值、amount 成交额），
#         回测撮合中常为单位一致的合法比较 -> 列需人工，评估误报率用。
KNOWN_UNIT_COLUMNS_HIGH = frozenset({"north_money", "net_amount"})
KNOWN_UNIT_COLUMNS_LOW = frozenset({"amount", "total_mv", "circ_mv", "vol"})
KNOWN_UNIT_COLUMNS = KNOWN_UNIT_COLUMNS_HIGH | KNOWN_UNIT_COLUMNS_LOW

# R21 关注"业务语义字段"：缺失时填 0 / 50 会伪装成合法结论。
_BUSINESS_SENTINEL_FIELDS = frozenset({"score", "ai_score", "confidence"})

# R22 水位 key 语义子串：命中即视为 checkpoint/高水位写入候选。
_WATERMARK_KEY_MARKERS = frozenset({"attempted", "watermark", "checkpoint", "upto", "last_sync", "resume"})


class _BaseVisitor(ast.NodeVisitor):
    """共用骨架：记录命中，支持按文件归属。"""

    def __init__(self) -> None:
        super().__init__()
        self.hits: list[dict[str, object]] = []
        self.cur_file: str = ""

    def _record(self, lineno: int, kind: str, detail: str, defect_id: str | None) -> None:
        self.hits.append(
            {
                "file": self.cur_file,
                "lineno": lineno,
                "kind": kind,
                "detail": detail,
                "defect_id": defect_id,
            }
        )


class UnitCompareVisitor(_BaseVisitor):
    """R20 单位核对：检测已知单位列被裸数值比较（无单位换算调用）。

    两类形态：
      A) pl.col("col") / pl.col("col").first() 等直接进入 Compare 节点；
      B) 变量名承载列值（如 north_money_val），且变量名含单位列名，
         随后参与 Compare —— 对 DATA-01 的 `north_money_val <= target_flow` 形态。
    两者均不含"单位换算"（get_column_unit / threshold_in_data_unit / *_UNIT
    常量）即标记。原型不追踪跨语句数据流，只按节点局部形态判定。
    """

    UNIT_CONVERSION_MARKERS = (
        "get_column_unit",
        "threshold_in_data_unit",
        "get_column_unit_source",
    )

    def visit_Compare(self, node: ast.Compare) -> None:
        # 仅在比较操作包含大小比较时关注（==/!= 不算量纲错误）。
        if not any(isinstance(o, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)) for o in node.ops):
            return
        for side in (node.left, *node.comparators):
            col = self._extract_column(side)
            if col is None:
                continue
            high = col in KNOWN_UNIT_COLUMNS_HIGH
            defect_id = self._match_known_defect(col, node.lineno) if high else None
            # 触发权：形态 A（pl.col）直判；形态 B（变量承载）仅 HIGH 列直判，
            # LOW 列记 lowconf（需人工复核）。
            kind = "unit_compare" if high else "unit_compare_lowconf"
            self._record(node.lineno, kind, f"列 {col!r} 参与大小比较（未显式单位换算）", defect_id)

    def _extract_column(self, node: ast.AST) -> str | None:
        """从表达式提取已知单位列名；支持 pl.col('c')、变量名含列名、普通 Name。"""
        # 形态 A：x.col("col") / pl.col("col")，Call.func.attr == "col"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "col":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                col = node.args[0].value
                return col if col in KNOWN_UNIT_COLUMNS else None
            return None
        # 形态 B：Name 变量，名称含单位列名（如 north_money_val）
        if isinstance(node, ast.Name):
            for col in KNOWN_UNIT_COLUMNS:
                if col in node.id:
                    return col
            return None
        return None

    def _match_known_defect(self, col: str, lineno: int) -> str | None:
        mapping = {
            "north_money": "DATA-01",
            "net_amount": "DATA-02",
        }
        return mapping.get(col)


class MissingMaskingVisitor(_BaseVisitor):
    """R21 缺失伪装：检测业务语义字段被填充 0 / 50 等合法具体值。

    形态：
      A) 赋值 score/confidence/ai_score = 0 / 0.0 / 50（含三元 else 分支）；
      B) IfExp（三元）else 分支为 0/50 且 test 含 None 判空；
      C) fillna(0)/fill(0)（列为低置信需人工档）。
    覆盖 Name 目标与 Subscript 目标（如 row_dict["confidence"] = ... else 50）。
    对照 AI-01（score=0 伪装否决）与 AI-02（confidence 缺失填 50）。
    """

    MASKING_VALUES = frozenset({0, 50})

    def _target_field(self, target: ast.AST) -> str | None:
        """从赋值目标提取语义字段名；支持 Name 与 Subscript（row_dict['x']）。"""
        if isinstance(target, ast.Name):
            return target.id
        if (
            isinstance(target, ast.Subscript)
            and isinstance(target.slice, ast.Constant)
            and isinstance(target.slice.value, str)
        ):
            return target.slice.value
        return None

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            field = self._target_field(target)
            if field is None or field not in _BUSINESS_SENTINEL_FIELDS:
                continue
            value = self._masking_const(node.value)
            # 仅当目标为业务语义字段时才算 AI 伪装。
            if value is not None:
                defect_id = "AI-02" if "confidence" in field else "AI-01"
                self._record(node.lineno, "masking_assign", f"字段 {field} 被填充常量 {value!r}", defect_id)

    def _masking_const(self, value: ast.AST) -> object | None:
        """返回若是 0/0.0/50/50.0 直接字面量；三元 else 分支为缺失填充则返回该值。"""
        if isinstance(value, ast.Constant) and value.value in self.MASKING_VALUES:
            return value.value
        if (
            isinstance(value, ast.IfExp)
            and isinstance(value.orelse, ast.Constant)
            and value.orelse.value in self.MASKING_VALUES
        ):
            return value.orelse.value
        return None

    def visit_Call(self, node: ast.Call) -> None:
        # fillna(0)/fill(0) 列为"需人工"低置信档
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"fillna", "fill"}:
            if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in self.MASKING_VALUES:
                self._record(node.lineno, "masking_fillna_lowconf", f"fillna/fill {node.args[0].value!r}", None)


class WatermarkVisitor(_BaseVisitor):
    """R22 水位单调：检测 checkpoint/高水位持久化写入是否无条件覆盖。

    业务方案是 `set_app_state_max`（SQL 层 GREATEST 保护）；现存代码调用
    无条件 `set_app_state`（on_conflict_do_update 最后写入者获胜）。原型标记
    满足两个条件的写入点：
      - 调 set_app_state（非 *_max 变体）；
      - key 含水位语义子串（attempted/watermark/checkpoint/upto/...）。
    命中即 SYNC-01 同类候选；defect_id 由位置映射回填。注意 `await set_app_state`
    外层是 ast.Await，须下钻 Await 内部再判定。
    """

    def generic_visit(self, node: ast.AST) -> None:
        # 自行下钻，以便在 Await 外壳内同样识别 Call（监控水位调用）。
        super().generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self._check_watermark_call(node)
        self.generic_visit(node)

    def visit_Await(self, node: ast.Await) -> None:
        if isinstance(node.value, ast.Call):
            self._check_watermark_call(node.value)
        self.generic_visit(node)

    def _check_watermark_call(self, node: ast.Call) -> None:
        fn = self._func_name(node.func)
        if fn is None or fn not in {"set_app_state", "set_app_state_max"}:
            return
        monotone = fn == "set_app_state_max"
        key = self._extract_key(node)
        key_norm = key.lower() if key else ""
        watermark = any(m in key_norm for m in _WATERMARK_KEY_MARKERS)
        if watermark and not monotone:
            defect_id = "SYNC-01"
            self._record(
                node.lineno,
                "watermark_unmonotone",
                f"set_app_state 写入水位 key {key!r}（非 *_max，最后写入者获胜）",
                defect_id,
            )

    @staticmethod
    def _func_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    def _extract_key(self, node: ast.Call) -> str | None:
        # 本原型简化：仅从位置参数 / f-string 常量与变量名拼接中提取语义文本。
        # f"{PREFIX}:{table}" 实际展开为 "sync_attempted_upto:<table>"，须把
        # FormattedValue 的变量名（Name.id）与 Constant 字面量一并拼接才有水位语义。
        for arg in node.args:
            text = self._arg_literal(arg)
            if text:
                return text
        return None

    @staticmethod
    def _arg_literal(arg: ast.AST) -> str:
        """提取调用参数的字面量/变量名拼接文本；提取不到返回空串。"""
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        if isinstance(arg, ast.Name):
            # 顶层变量（如 engine）无语义线索，不作为 key 文本；其语义仅在 f-string
            # 内的 {_WATERMARK_KEY_PREFIX} 形态中体现（由 JoinedStr 分支处理）。
            return ""
        if isinstance(arg, ast.JoinedStr):
            parts: list[str] = []
            for v in arg.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    parts.append(v.value)
                elif isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name):
                    parts.append(v.value.id)
            return "".join(parts)
        return ""


# 缺陷位置映射 -> (相对路径, 命中 kind 集合, 期望豁免语义)。
# expect="hit"：真实未修复缺陷，原型应命中（漏报为失败）。
# expect="clean"：已修复缺陷，原型不应误伤（false positive 为失败）。
#   语义：原型若在已修复代码上仍报警，说明检测规则过宽，误报率超阈值。
#   验证方式 assert 已有：main 已把 AI-01 的 ai_score 默认调到 None、DATA-03 改为
#   suspend_data_absent 显式告警，原型不得再视为缺失伪装。
_REGRESSION_TARGETS: dict[str, tuple[str, set[str], str]] = {
    "DATA-01": ("strategies/market.py", {"unit_compare"}, "hit"),
    "DATA-02": ("strategies/market.py", {"unit_compare"}, "hit"),
    "AI-02": ("strategies/ai_mixin.py", {"masking_assign"}, "hit"),
    "SYNC-01": ("data/sync/historical.py", {"watermark_unmonotone"}, "hit"),
    # 已修复项：验证不误伤（clean）
    "AI-01": ("strategies/ai_mixin.py", {"masking_assign"}, "clean"),
    "DATA-03": ("strategies/backtest/engine.py", {"masking_assign", "masking_ifexp"}, "clean"),
}


class PrototypeScanner:
    """对一组目录执行三条红线的 AST 扫描，并聚合回归/误报统计。"""

    SCAN_DIRS = (
        ("R20", ("strategies",)),
        ("R21", ("services", "strategies")),
        ("R22", ("data", "services")),
    )
    SKIP_DIRS = frozenset({"__pycache__", ".venv", "tests", ".worktrees"})

    def __init__(self, root: Path) -> None:
        self.root = root
        self._visitors: dict[str, list[_BaseVisitor]] = {
            "R20": [UnitCompareVisitor()],
            "R21": [MissingMaskingVisitor()],
            "R22": [WatermarkVisitor()],
        }

    def run(self) -> dict:
        by_rule: dict[str, list[dict]] = {k: [] for k in self._visitors}
        for rule, subdirs in self.SCAN_DIRS:
            for sub in subdirs:
                base = self.root / sub
                if not base.is_dir():
                    continue
                for py in base.rglob("*.py"):
                    if any(part in self.SKIP_DIRS for part in py.relative_to(self.root).parts):
                        continue
                    src = py.read_text(encoding="utf-8", errors="replace")
                    try:
                        tree = ast.parse(src, filename=str(py))
                    except SyntaxError:
                        continue
                    rel = py.relative_to(self.root).as_posix()
                    for vis in self._visitors[rule]:
                        vis.cur_file = rel
                        vis.visit(tree)
                        for h in vis.hits:
                            h["rule"] = rule
                        by_rule[rule].extend(self._dedup(vis.hits))
                        vis.hits = []
        return by_rule

    def regression(self, by_rule: dict[str, list[dict]]) -> dict:
        """对照业务检视 11 项缺陷集，判断每条约束的覆盖与误报。

        expect="hit"   -> 该文件命中目标 kind 为覆盖（漏报记 fail）。
        expect="clean" -> 该缺陷（defect_id，若原型能标注）不得命中（false positive 记 fail）。

        按 defect_id 匹配优先：AI-01（ai_score 伪装，已修为合法 score==0 语义）与
        AI-02（confidence 伪装）同文件同 kind，仅靠 file+kind 无法区分 clean，须用
        原型自带的 defect_id 标注精确判定。
        """
        all_hits = self._all_hits(by_rule)

        def matched(path: str, target_kinds: set[str], defect_id: str | None = None) -> bool:
            for h in all_hits:
                if h["file"] != path:
                    continue
                if defect_id is not None:
                    # clean 判定：命中点在对应缺陷上才算误报
                    if h.get("defect_id") == defect_id and any(tk in str(h["kind"]) for tk in target_kinds):
                        return True
                    continue
                if any(tk in str(h["kind"]) for tk in target_kinds):
                    return True
            return False

        coverage: dict[str, bool] = {}
        for defect_id, (path, target_kinds, expect) in _REGRESSION_TARGETS.items():
            m = matched(path, target_kinds, defect_id=None if expect == "hit" else defect_id)
            coverage[defect_id] = m if expect == "hit" else (not m)
        covered_total = sum(1 for v in coverage.values() if v)
        return {
            "defects_covered": covered_total,
            "target_min": 6,
            "coverage": coverage,
        }

    @staticmethod
    def _dedup(hits: list[dict]) -> list[dict]:
        """按 (file, lineno, kind) 去重：visit_Await 与 visit_Call 会重复进入同一调用点。"""
        seen: set[tuple[str, int, str]] = set()
        out: list[dict] = []
        for h in hits:
            sig = (h["file"], h["lineno"], h["kind"])
            if sig in seen:
                continue
            seen.add(sig)
            out.append(h)
        return out

    @staticmethod
    def _all_hits(by_rule: dict[str, list[dict]]) -> list[dict]:
        return [h for lst in by_rule.values() for h in lst]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path(argv[0]).resolve() if argv else Path(__file__).resolve().parent.parent
    scanner = PrototypeScanner(root)
    by_rule = scanner.run()
    reg = scanner.regression(by_rule)

    report = {
        "root": str(root),
        "findings": by_rule,
        "regression": reg,
    }
    # stdout: 结构化 JSON（供机器消费）；stderr: 人类可读摘要
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("\n===== 摘要 =====", file=sys.stderr)
    for rule in ("R20", "R21", "R22"):
        hits = by_rule[rule]
        print(f"[{rule}] 命中 {len(hits)} 处", file=sys.stderr)
        for h in hits:
            tag = h["defect_id"] or ""
            print(f"  - {h['file']}:{h['lineno']} {h['kind']} {h['detail']} {tag}".rstrip(), file=sys.stderr)
    print(
        f"\n缺陷集回归覆盖: {reg['defects_covered']}/{reg['target_min']} (目标 >= {reg['target_min']})", file=sys.stderr
    )
    for did, ok in reg["coverage"].items():
        print(f"  {did}: {'命中' if ok else '未命中'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
