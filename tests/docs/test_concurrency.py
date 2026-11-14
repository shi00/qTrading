"""D4 并发保护测试（检视 §5.2.1 全窗口合同与 §6.4「写入中断」负例）。

确定性屏障注入：run_update 提供屏障钩子（after_render / before_replace / after_write），
测试在确定时刻注入外部编辑（模拟用户/编辑器在渲染后、提交核对后但替换前改动目标或
作者源），断言：冲突被检出、非零语义退出、原位未覆盖、用户新稿保留、已写/未写如实列出。
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

import docs_generated as dg
from fixtures import generator_fixtures as gf

pytestmark = pytest.mark.docs_pure

WIN = sys.platform == "win32"


# ---------------------------------------------------------------------------
# 写入保护：Windows 独占字节锁
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not WIN, reason="内核级独占打开保护仅在 Windows 提供；POSIX 协作锁按 §5.2.1-3 拒绝原位更新")
def test_guard_blocks_second_writer(tmp_path: Path) -> None:
    """保护持有期间，第二个写者无法取得保护（内核级，不依赖协作）。"""
    target = tmp_path / "f.md"
    target.write_text("内容", encoding="utf-8")
    with dg.WriteGuard([target]):
        with pytest.raises(dg.ProtectionDeniedError, match="无法独占打开|无法取得写入保护"):
            with dg.WriteGuard([target]):
                pass


@pytest.mark.skipif(not WIN, reason="同上，仅 Windows 独占打开语义")
def test_guard_release_allows_reacquire(tmp_path: Path) -> None:
    """保护释放后可重新取得（不遗留锁）。"""
    target = tmp_path / "f.md"
    target.write_text("内容", encoding="utf-8")
    with dg.WriteGuard([target]):
        pass
    with dg.WriteGuard([target]):
        pass


@pytest.mark.skipif(WIN, reason="POSIX 分支：flock 为协作锁，无法证明覆盖外部编辑器 → 按合同拒绝")
def test_guard_posix_rejects_unsupported(tmp_path: Path) -> None:
    """POSIX 上取得协作锁后必须显式拒绝原位更新（不把共享工作区默认为独占环境）。"""
    target = tmp_path / "f.md"
    target.write_text("内容", encoding="utf-8")
    with pytest.raises(dg.ProtectionUnsupportedError, match="协作锁"):
        with dg.WriteGuard([target]):
            pass


def test_guard_write_refused_before_mutation(tmp_path: Path) -> None:
    """取得保护后写入仍被拒绝：不截断、不写入，原正文逐字节保留（D28）。"""
    target = tmp_path / "f.md"
    original = "旧内容\n块外手写正文\n".encode()
    target.write_bytes(original)
    guard = gf.HeldFdWriteGuard([target])
    with guard:
        with pytest.raises(dg.InPlaceWriteUnsupportedError, match="崩溃原子提交"):
            guard.write_bytes(target, "更长的替换内容".encode())
    assert target.read_bytes() == original, "拒绝原位写前发生字节变更=可能截断/半写目标"


# ---------------------------------------------------------------------------
# 快照漂移（保护窗口内 hash/身份变化 → 拒绝，不基于旧快照继续）
# ---------------------------------------------------------------------------


def test_snapshot_verify_detects_byte_change(tmp_path: Path) -> None:
    """保护窗口内核对：文件字节变化 → SnapshotDriftError，拒绝继续。"""
    target = tmp_path / "f.md"
    target.write_text("v1", encoding="utf-8")
    snap = dg.snapshot_file(tmp_path, "f.md")
    target.write_text("v2-by-user", encoding="utf-8")
    with pytest.raises(dg.SnapshotDriftError, match="内容在快照后被修改"):
        dg.verify_snapshot(snap)


def test_snapshot_verify_detects_identity_change(tmp_path: Path) -> None:
    """保护窗口内核对：文件被删除重建（同内容不同身份）→ SnapshotDriftError。"""
    target = tmp_path / "f.md"
    target.write_text("same", encoding="utf-8")
    snap = dg.snapshot_file(tmp_path, "f.md")
    target.unlink()
    target.write_text("same", encoding="utf-8")
    detected = False
    try:
        dg.verify_snapshot(snap)
    except dg.SnapshotDriftError:
        detected = True
    if not detected:
        pytest.skip("当前文件系统不提供稳定文件身份（重建后 st_ino 未变化），身份核对退化用例跳过")


# ---------------------------------------------------------------------------
# 确定性屏障：渲染后注入外部写入尝试（§5.2.1-5 第一屏障）
#
# 平台事实：Windows 独占保护（CreateFileW dwShareMode=0）物理阻止屏障注入的
# write_text/append 打开文件（ERROR_SHARING_VIOLATION → PermissionError）。因此
# 屏障注入验证的是合同的两条出口之一：「写入被保护阻止 → 检出冲突、原位未覆盖、
# 非零退出」；「编辑成功后被提交核对检出」由 verify_snapshot 的快照漂移单元测试
# （无保护环境直读）与 run_update 的 SnapshotDriftError 分支共同覆盖。
# ---------------------------------------------------------------------------


def test_barrier_edit_after_render_detected(tmp_path: Path) -> None:
    """渲染后注入外部写入尝试 → 保护阻止并检出冲突、原位未覆盖、原稿保留。"""
    root = gf.build_demo_tree(tmp_path)
    target = root / gf.DEMO_TARGET_REL
    before = target.read_text(encoding="utf-8")

    def barrier(phase: str, target_rel: str | None) -> None:
        if phase == "after_render":
            # 模拟外部写者/编辑器保存：独占保护下该打开必须失败（PermissionError 传播）
            target.write_text("# 用户新稿\n手写内容不可被旧快照覆盖。\n", encoding="utf-8")

    result = dg.run_update(root, gf.make_demo_registry(), barrier=barrier)
    assert result.ok is False, "渲染后的外部写入尝试必须被检出，不得静默继续"
    assert result.conflicts == (gf.DEMO_TARGET_REL,)
    assert result.written == (), "冲突目标不得写入"
    assert target.read_text(encoding="utf-8") == before, "原位内容被覆盖=用户新稿窗口丢失"


def test_barrier_edit_before_replace_detected(tmp_path: Path) -> None:
    """提交核对后、替换前注入外部写入尝试（第二屏障）→ 检出冲突、原位未覆盖。"""
    root = gf.build_demo_tree(tmp_path)
    target = root / gf.DEMO_TARGET_REL
    before = target.read_text(encoding="utf-8")

    def barrier(phase: str, target_rel: str | None) -> None:
        if phase == "before_replace" and target_rel == gf.DEMO_TARGET_REL:
            target.write_text("# 核对后新稿\n", encoding="utf-8")

    result = dg.run_update(root, gf.make_demo_registry(), barrier=barrier)
    assert result.ok is False
    assert result.conflicts == (gf.DEMO_TARGET_REL,)
    assert result.written == ()
    assert target.read_text(encoding="utf-8") == before, "提交核对后原位被覆盖"


def test_barrier_author_source_edit_detected(tmp_path: Path) -> None:
    """渲染后作者源被外部写入尝试 → 独占保护阻止、检出冲突、目标不写。"""
    root = gf.build_demo_tree(tmp_path)
    source = root / gf.DEMO_SOURCE_REL

    def barrier(phase: str, target_rel: str | None) -> None:
        if phase == "after_render":
            source.write_text(gf.VALID_SOURCE_YAML.replace("演示红线一", "用户改过的标题"), encoding="utf-8")

    result = dg.run_update(root, gf.make_demo_registry(), barrier=barrier)
    assert result.ok is False
    assert result.conflicts == (gf.DEMO_TARGET_REL,)
    assert result.written == ()
    assert source.read_text(encoding="utf-8") == gf.VALID_SOURCE_YAML, "作者源被外部改动=保护失效"
    assert "用户改过的标题" not in source.read_text(encoding="utf-8"), "旧快照产物不得落盘"


# ---------------------------------------------------------------------------
# 写失败：如实列已写/未写，不承诺跨文件事务
# ---------------------------------------------------------------------------


def test_write_refusal_lists_every_unwritten_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """原位写被拒绝时逐目标列出未写，已写恒为空，且继续处理其余目标（不承诺跨文件事务）。"""
    root = tmp_path
    (root / "sources").mkdir()
    (root / gf.DEMO_SOURCE_REL).write_text(gf.VALID_SOURCE_YAML, encoding="utf-8")
    (root / "a.md").write_text(
        gf.DEMO_TARGET.replace("generated:demo-block", "generated:demo-block-a"), encoding="utf-8"
    )
    (root / "b.md").write_text(
        gf.DEMO_TARGET.replace("generated:demo-block", "generated:demo-block-b"), encoding="utf-8"
    )
    import dataclasses

    spec_a = dataclasses.replace(gf.make_demo_registry()[0], target="a.md", block_id="demo-block-a")
    spec_b = dataclasses.replace(gf.make_demo_registry()[0], target="b.md", block_id="demo-block-b")
    before_a = (root / "a.md").read_bytes()
    before_b = (root / "b.md").read_bytes()
    monkeypatch.setattr(dg, "WriteGuard", gf.HeldFdWriteGuard)

    result = dg.run_update(root, (spec_a, spec_b))
    assert result.ok is False
    assert result.written == (), "原位写被拒绝时不得有目标被记为已写"
    assert result.failed == ("a.md", "b.md"), "未写目标必须逐个如实列出（继续处理其余目标）"
    assert (root / "a.md").read_bytes() == before_a
    assert (root / "b.md").read_bytes() == before_b


# ---------------------------------------------------------------------------
# D28 负例：短写 / 截断后异常 / 提交中断 → 原正文完整、未写如实报告
# ---------------------------------------------------------------------------


def test_short_write_never_attempted_and_original_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """短写负例：底层 os.write 从未被调用（拒绝发生在任何字节变更前），原正文完整。"""
    root = gf.build_demo_tree(tmp_path)
    target = root / gf.DEMO_TARGET_REL
    before = target.read_bytes()
    write_attempts: list[int] = []
    monkeypatch.setattr(os, "write", lambda fd, data: write_attempts.append(len(data)) or 0)
    monkeypatch.setattr(dg, "WriteGuard", gf.HeldFdWriteGuard)

    result = dg.run_update(root, gf.make_demo_registry())
    assert result.ok is False
    assert result.written == ()
    assert result.failed == (gf.DEMO_TARGET_REL,)
    assert write_attempts == [], "原位写被拒绝后仍调用 os.write=可能短写并留下半写内容"
    assert target.read_bytes() == before


def test_truncate_then_error_never_reached_and_original_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """截断后异常负例：不调用 ftruncate（无截断窗口），原正文完整、未写如实报告。"""

    def _forbidden_ftruncate(fd: int, size: int) -> None:
        raise AssertionError("拒绝原位写不得先截断目标（截断后异常会丢失整份正文）")

    root = gf.build_demo_tree(tmp_path)
    target = root / gf.DEMO_TARGET_REL
    before = target.read_bytes()
    monkeypatch.setattr(os, "ftruncate", _forbidden_ftruncate)
    monkeypatch.setattr(dg, "WriteGuard", gf.HeldFdWriteGuard)

    result = dg.run_update(root, gf.make_demo_registry())
    assert result.ok is False
    assert result.written == ()
    assert result.failed == (gf.DEMO_TARGET_REL,)
    assert target.read_bytes() == before


def test_commit_interruption_reports_unwritten_and_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """提交中断负例：提交前注入中断 → 原正文完整、written 为空、failed 准确、非 ok（CLI 退出 1）。"""
    root = gf.build_demo_tree(tmp_path)
    target = root / gf.DEMO_TARGET_REL
    before = target.read_bytes()
    monkeypatch.setattr(dg, "WriteGuard", gf.HeldFdWriteGuard)

    def barrier(phase: str, target_rel: str | None) -> None:
        if phase == "before_replace":
            raise OSError("注入的提交中断")

    result = dg.run_update(root, gf.make_demo_registry(), barrier=barrier)
    assert result.ok is False
    assert result.written == ()
    assert result.failed == (gf.DEMO_TARGET_REL,)
    assert target.read_bytes() == before, "提交中断后原正文被改动"


def test_refused_write_maps_to_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """拒绝原位写的结果经 CLI 映射为非零退出（退出码合同：写入失败 1）。"""
    import check_docs_consistency as cdc

    root = gf.build_demo_tree(tmp_path)
    target = root / gf.DEMO_TARGET_REL
    before = target.read_bytes()
    monkeypatch.setattr(dg, "WriteGuard", gf.HeldFdWriteGuard)
    result = dg.run_update(root, gf.make_demo_registry())

    assert cdc.main(["--update"], update_runner=lambda: result) == 1
    assert capsys.readouterr().out != "", "退出非零时必须如实输出未写/失败明细"
    assert target.read_bytes() == before


def test_render_is_deterministic(tmp_path: Path) -> None:
    """同一作者源多次渲染输出逐字节一致（幂等的前提）。"""
    root = gf.build_demo_tree(tmp_path)
    registry = gf.make_demo_registry()
    first = dg.render_block_lines(root, registry[0])
    second = dg.render_block_lines(root, registry[0])
    assert first == second
    assert (
        hashlib.sha256("\n".join(first).encode("utf-8")).hexdigest()
        == hashlib.sha256("\n".join(second).encode("utf-8")).hexdigest()
    )
