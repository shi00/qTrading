"""D18-T01：以模拟 gh CLI 执行 sidecar 发布 workflow 的状态机逻辑。

与 ``test_sidecar_release_workflow.py`` 的静态断言不同，本文件从
``sidecar.yml`` 提取 ``publish-release`` 的内联 shell，在临时目录以真实 bash
执行，并由假 ``gh`` 记录调用顺序与 Release/资产对象状态。

覆盖：已发布拒绝、draft 复用、无 Release 新建 draft、API 失败 fail-closed、
资产少件/摘要不符拒绝发布、同 tag 重跑（published 拒绝 / draft 恢复）、
资产上传失败留下 draft。矩阵腿失败/取消的阻断语义由
``test_sidecar_release_workflow.py`` 的结构断言守护（无法在本地重放
GitHub needs 聚合）。

不触碰真实 GitHub：假 gh 仅读写临时世界文件；任何真实网络/真实 gh 调用都会
被 PATH 守卫拦截。
"""

from __future__ import annotations

import re
import shutil
import sys

import pytest

from tests.unit.sidecar_release_harness import (
    LOCAL_VERIFY_STEP,
    PUBLISH_STEP,
    REMOTE_VERIFY_STEP,
    STATE_STEP,
    UPLOAD_STEP,
    ChainResult,
    SidecarReleaseHarness,
    StepResult,
    make_asset,
    make_release,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.meta,
    pytest.mark.skipif(
        sys.platform != "linux"
        or any(shutil.which(tool) is None for tool in ("bash", "jq", "sha256sum", "awk", "find")),
        reason="publisher 在 ubuntu-latest 执行；本机需 bash/jq/GNU find 才能重放",
    ),
]

TAG = "sidecar-v1.2.3"
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _writes(h: SidecarReleaseHarness) -> list[dict[str, object]]:
    return h.write_calls()


def _creates(h: SidecarReleaseHarness) -> list[dict[str, object]]:
    return [call for call in h.calls if call["method"] == "POST" and str(call["path"]).endswith("/releases")]


def _patches(h: SidecarReleaseHarness) -> list[dict[str, object]]:
    return [call for call in h.calls if call["method"] == "PATCH"]


def _uploads(h: SidecarReleaseHarness) -> list[dict[str, object]]:
    return [call for call in h.calls if call["method"] == "POST" and "/assets" in str(call["path"])]


def _deletes(h: SidecarReleaseHarness) -> list[dict[str, object]]:
    return [call for call in h.calls if call["method"] == "DELETE"]


def _executed(chain: ChainResult) -> list[str]:
    return [result.name for result in chain.executed]


def _output(result: StepResult) -> str:
    """workflow 用 ``::error::`` 写 stdout（Actions 注解），错误文本不一定在 stderr。"""
    return result.stdout + result.stderr


def test_published_release_refuses_state_and_writes_nothing(tmp_path) -> None:
    """已发布 Release 存在：状态检查 exit 1，且后续无任何写调用。"""
    published = make_release(
        101,
        TAG,
        draft=False,
        assets=[make_asset(9000 + index, f"stale-{index}") for index in range(3)],
    )
    harness = SidecarReleaseHarness(tmp_path, tag=TAG, commit_sha=COMMIT, releases=[published])

    chain = harness.run_chain()

    assert _executed(chain) == [LOCAL_VERIFY_STEP, STATE_STEP]
    assert chain.executed[-1].exit_code == 1
    assert "release sidecar-v1.2.3 is already published" in _output(chain.executed[-1])
    assert _writes(harness) == []
    assert harness.world()["releases"][0]["draft"] is False


def test_existing_draft_is_reused_then_published_once(tmp_path) -> None:
    """仅有 draft：删除旧资产后复用同一 release id，不新建正式版。"""
    draft = make_release(
        77,
        TAG,
        draft=True,
        assets=[make_asset(5001, "stale-a"), make_asset(5002, "stale-b")],
    )
    harness = SidecarReleaseHarness(tmp_path, tag=TAG, commit_sha=COMMIT, releases=[draft])

    chain = harness.run_chain()

    assert _executed(chain) == [LOCAL_VERIFY_STEP, STATE_STEP, UPLOAD_STEP, REMOTE_VERIFY_STEP, PUBLISH_STEP]
    assert chain.executed[1].stdout and "Existing draft release" in chain.executed[1].stdout
    assert _creates(harness) == [], "复用 draft 时不得再次 POST /releases"
    deletes = _deletes(harness)
    assert len(deletes) == 2
    uploads = _uploads(harness)
    assert len(uploads) == 12
    patches = _patches(harness)
    assert len(patches) == 1
    assert patches[0]["fields"] == {"draft": False, "make_latest": True}
    # 写顺序：先清理旧资产 → 再上传 12 项 → 最后唯一一次 PATCH
    assert harness.calls.index(deletes[-1]) < harness.calls.index(uploads[0])
    assert harness.calls.index(uploads[-1]) < harness.calls.index(patches[0])

    release = harness.world()["releases"][0]
    assert release["id"] == 77
    assert release["draft"] is False
    assert len(release["assets"]) == 12


def test_no_release_creates_draft_first_and_publishes_once(tmp_path) -> None:
    """无 Release：先创建 draft=true，上传 12 资产，最后一次 PATCH 发布。"""
    harness = SidecarReleaseHarness(tmp_path, tag=TAG, commit_sha=COMMIT)

    chain = harness.run_chain()

    assert [result.exit_code for result in chain.executed] == [0, 0, 0, 0, 0]
    assert "No release exists" in chain.executed[1].stdout
    creates = _creates(harness)
    assert len(creates) == 1, "无 Release 时只允许创建一次 draft"
    assert creates[0]["fields"] == {
        "tag_name": TAG,
        "name": TAG,
        "draft": True,
        "prerelease": False,
        "generate_release_notes": True,
    }
    assert len(_uploads(harness)) == 12
    patches = _patches(harness)
    assert len(patches) == 1
    assert patches[0]["fields"] == {"draft": False, "make_latest": True}

    worlds = harness.world()["releases"]
    assert len(worlds) == 1
    assert worlds[0]["draft"] is False
    names = {asset["name"] for asset in worlds[0]["assets"]}
    assert names == set(harness.expected_asset_names())
    for asset in worlds[0]["assets"]:
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", asset["digest"])


@pytest.mark.parametrize(
    ("mode", "output_fragment"),
    [
        ("http500", "HTTP 500"),
        ("nonjson", "unexpected response"),
    ],
)
def test_release_query_failure_is_fail_closed(tmp_path, mode: str, output_fragment: str) -> None:
    """API 查询 500 / 非 JSON：状态检查失败即中止，不进入任何写操作。"""
    harness = SidecarReleaseHarness(
        tmp_path,
        tag=TAG,
        commit_sha=COMMIT,
        failure={"path_contains": "releases?per_page=100", "mode": mode, "occurrence": 1},
    )

    chain = harness.run_chain()

    assert _executed(chain) == [LOCAL_VERIFY_STEP, STATE_STEP]
    assert chain.executed[-1].exit_code != 0
    assert output_fragment in _output(chain.executed[-1])
    assert _writes(harness) == []
    assert all(call["subcommand"] == "api" for call in harness.calls), "发布链只允许 gh api，不得回退 gh release"


def test_remote_verification_api_failure_never_publishes(tmp_path) -> None:
    """第二次 releases 查询（draft 复验）失败：已有上传但绝不 PATCH 发布。"""
    harness = SidecarReleaseHarness(
        tmp_path,
        tag=TAG,
        commit_sha=COMMIT,
        failure={"path_contains": "releases?per_page=100", "mode": "http500", "occurrence": 2},
    )

    chain = harness.run_chain()

    assert _executed(chain) == [LOCAL_VERIFY_STEP, STATE_STEP, UPLOAD_STEP, REMOTE_VERIFY_STEP]
    assert chain.executed[-1].exit_code != 0
    assert len(_patches(harness)) == 0
    release = harness.world()["releases"][0]
    assert release["draft"] is True, "失败后最多留下 draft"


def test_local_asset_shortfall_refuses_before_any_release_write(tmp_path) -> None:
    """本地 12 项少件：下载校验失败，状态检查与所有写操作均不执行。"""
    harness = SidecarReleaseHarness(tmp_path, tag=TAG, commit_sha=COMMIT)
    (harness.assets_dir / "qtrading-pg-sidecar-macos-aarch64" / "sidecar-manifest-macos.json").unlink()

    chain = harness.run_chain()

    assert _executed(chain) == [LOCAL_VERIFY_STEP]
    assert chain.executed[0].exit_code != 0
    assert "unexpected asset set" in _output(chain.executed[0])
    assert _writes(harness) == []


def test_remote_asset_shortfall_refuses_publish(tmp_path) -> None:
    """服务端 12 项少件：draft 复验拒绝，PATCH 发布不执行。"""
    harness = SidecarReleaseHarness(
        tmp_path,
        tag=TAG,
        commit_sha=COMMIT,
        drop_asset_names=["qtrading-pg-sidecar-linux-aarch64"],
    )

    chain = harness.run_chain()

    assert _executed(chain)[-1] == REMOTE_VERIFY_STEP
    assert chain.executed[-1].exit_code != 0
    assert "draft asset set mismatch" in _output(chain.executed[-1])
    assert len(_uploads(harness)) == 12, "失败必须发生在服务端复验，而不是上传提前中止"
    assert len(_patches(harness)) == 0
    assert harness.world()["releases"][0]["draft"] is True


def test_server_digest_mismatch_refuses_publish(tmp_path) -> None:
    """服务端 digest 与本地字节不符：draft 复验拒绝发布。"""
    harness = SidecarReleaseHarness(
        tmp_path,
        tag=TAG,
        commit_sha=COMMIT,
        corrupt_digests={"qtrading-pg-sidecar-windows-x86_64.exe": "sha256:" + "0" * 64},
    )

    chain = harness.run_chain()

    assert _executed(chain)[-1] == REMOTE_VERIFY_STEP
    assert chain.executed[-1].exit_code != 0
    assert "digest mismatch" in _output(chain.executed[-1])
    assert len(_uploads(harness)) == 12
    assert len(_patches(harness)) == 0
    assert harness.world()["releases"][0]["draft"] is True


def test_asset_upload_failure_leaves_draft_and_never_publishes(tmp_path) -> None:
    """draft 资产上传失败：中止上传，PATCH 不执行，服务端仍是 draft。"""
    harness = SidecarReleaseHarness(
        tmp_path,
        tag=TAG,
        commit_sha=COMMIT,
        fail_upload_names=["qtrading-pg-sidecar-linux-x86_64"],
    )

    chain = harness.run_chain()

    assert _executed(chain) == [LOCAL_VERIFY_STEP, STATE_STEP, UPLOAD_STEP]
    assert chain.executed[-1].exit_code != 0
    assert len(_patches(harness)) == 0
    release = harness.world()["releases"][0]
    assert release["draft"] is True
    assert len(release["assets"]) == 3, "失败前已上传的资产保留在 draft（首个平台 3 项）"


def test_rerun_after_publish_is_refused_by_state_check(tmp_path) -> None:
    """同 tag 重跑：成功发布后再次运行，状态检查拒绝且无新增写调用。"""
    harness = SidecarReleaseHarness(tmp_path, tag=TAG, commit_sha=COMMIT)
    first = harness.run_chain()
    assert [result.exit_code for result in first.executed] == [0, 0, 0, 0, 0]
    baseline_writes = len(_writes(harness))
    assert baseline_writes == 14  # 1 次创建 draft + 12 次上传 + 1 次发布

    rerun = harness.run_chain([LOCAL_VERIFY_STEP, STATE_STEP])

    assert rerun.executed[-1].exit_code == 1
    assert "already published" in _output(rerun.executed[-1])
    assert len(_writes(harness)) == baseline_writes, "重跑被拒时不得产生任何写调用"
    assert len(harness.world()["releases"]) == 1, "重跑不得产生第二个 Release 对象"
    assert harness.world()["releases"][0]["draft"] is False


def test_annotated_tag_is_peeled_to_commit_before_release_writes(tmp_path) -> None:
    """annotated tag：先解析 tag 对象到 commit，仍通过本地校验后才允许写。"""
    harness = SidecarReleaseHarness(
        tmp_path,
        tag=TAG,
        commit_sha=COMMIT,
        annotated_tag=True,
    )

    chain = harness.run_chain([LOCAL_VERIFY_STEP])

    assert chain.executed[0].exit_code == 0
    assert any("/git/tags/" in str(call["path"]) for call in harness.calls)
    assert harness.world()["releases"] == []


def test_tag_commit_mismatch_refuses_before_release_writes(tmp_path) -> None:
    """触发器 commit 与远端 tag 锚定 commit 不一致：本地校验失败，不创建 Release。"""
    harness = SidecarReleaseHarness(
        tmp_path,
        tag=TAG,
        commit_sha=COMMIT,
        tag_commit_sha="f" * 40,
    )

    chain = harness.run_chain([LOCAL_VERIFY_STEP])

    assert chain.executed[0].exit_code == 1
    assert "does not match trigger commit" in _output(chain.executed[0])
    assert _writes(harness) == []
