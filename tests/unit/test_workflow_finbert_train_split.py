"""finbert-train.yml 最小权限拆分（FT-06）静态断言。

守护目标（检视报告 FT-06，P2 确定缺陷）：
- 训练/导出/Gate job（train-export-gate）permissions 仅有 ``contents: read``，
  依赖安装 / 语料下载 / 训练 / 导出 / Gate 全链路期间 job token 无写仓库能力；
- 发布 job（publish-release）单独授予 ``contents: write``，且仅在
  train-export-gate 结果为 success 时运行（Gate 失败 → 发布 job 跳过）；
- 发布 job 只消费 job1 上传的发布 artifact（最小文件集），下载后先
  ``sha256sum -c`` 本地复验再发布，不 checkout、不执行仓库脚本；
- 发布步骤版本不可变（FT-05）：Release tag 已存在时拒绝发布并失败
  （exit 1 + 明确指引），不提供 ``--clobber`` 部分更新（只换资产二进制、
  不换 note 会令 lineage 静默错位）；``gh release create`` 仅在 tag
  不存在分支可达。

依赖与环境基线（FT-08，P3）：
- 训练依赖安装步骤全部精确 pin（``name==version``），禁止范围约束
  （``>=``/``<=``/``~=``/``!=``）与未版本化 bare 包名——范围约束随 PyPI
  漂移引入训练数值行为差异（聚合精度/默认参数），使跨次训练 lineage
  比较失真；
- Release note 含运行时环境 lineage：runner 镜像（ImageOS/ImageVersion）、
  Python 版本、依赖版本（动态读取已安装 metadata，与 pip pin 单一事实源）、
  llama.cpp 工具链 commit（与 checkout ref 单一事实源）；
- llama.cpp 工具链由 tag 改为不可变 commit 锚定（tag v0.3.0 指向的提交——
  tag 可被上游移动/重打，仅记录 tag 无法定位实际拉取的提交）；
- runner 基线为滚动 ``ubuntu-latest``（权衡与兜底见 workflow 文件头注释），
  改固定 container 须有意变更本断言。

全部为 workflow YAML 的静态断言，不触发任何训练 / 发布副作用。
"""

import re
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.meta]

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "finbert-train.yml"

JOB1_NAME = "train-export-gate"
JOB2_NAME = "publish-release"

INSTALL_STEP_NAME = "Install training / export / gate dependencies"
NOTE_STEP_NAME = "Generate release note"

# FT-08：训练依赖全 pin 的最小包集（torch 经 PyTorch CPU index 单独安装，
# 其余与其后安装的 llama-cpp-python 同一条 pip install 命令）。
PINNED_TRAINING_PACKAGES = frozenset(
    {
        "torch",
        "transformers",
        "accelerate",
        "numpy",
        "sentencepiece",
        "llama-cpp-python",
    }
)

# 精确 pin 规格：``name==version``（local version 如 2.9.1+cpu 亦合法）。
# fullmatch 天然排除 >= / <= / ~= / != / bare 包名等一切非精确 pin 形态。
PINNED_SPEC_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*==[A-Za-z0-9][A-Za-z0-9.+]*")

# FT-08：llama.cpp 工具链锚定的 commit（tag v0.3.0 指向的提交）。
# checkout ref 与 note lineage 均须逐字等于该值（tag 可被上游移动，commit 不可变）。
LLAMA_CPP_CHECKOUT_STEP = "Checkout llama.cpp v0.3.0"
LLAMA_CPP_COMMIT = "c1d0e7a004015f23bc0233470b747b596f29b264"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_top_level_permissions_are_read_only(workflow: dict) -> None:
    assert workflow["permissions"] == {"contents": "read"}


def test_job1_permissions_are_read_only(workflow: dict) -> None:
    jobs = workflow["jobs"]
    assert JOB1_NAME in jobs
    assert jobs[JOB1_NAME]["permissions"] == {"contents": "read"}


def test_job2_permissions_are_write(workflow: dict) -> None:
    jobs = workflow["jobs"]
    assert JOB2_NAME in jobs
    assert jobs[JOB2_NAME]["permissions"] == {"contents": "write"}


def test_job2_depends_on_job1_and_guards_on_success(workflow: dict) -> None:
    job2 = workflow["jobs"][JOB2_NAME]
    assert job2["needs"] == [JOB1_NAME]
    assert job2["if"] == f"needs.{JOB1_NAME}.result == 'success'"


def test_job1_exposes_outputs_for_publish(workflow: dict) -> None:
    outputs = workflow["jobs"][JOB1_NAME]["outputs"]
    assert outputs["model_version"] == "${{ steps.validate_inputs.outputs.model_version }}"
    assert outputs["release_tag"] == "${{ steps.validate_inputs.outputs.release_tag }}"


def test_job2_steps_order_download_verify_publish(workflow: dict) -> None:
    steps = workflow["jobs"][JOB2_NAME]["steps"]
    assert [s["name"] for s in steps] == [
        "Download release artifacts",
        "Verify checksums before publish",
        "Publish release (finbert-gguf-v*)",
    ]


def test_job2_verifies_checksums_before_publish(workflow: dict) -> None:
    steps = workflow["jobs"][JOB2_NAME]["steps"]
    verify = next(s for s in steps if s["name"] == "Verify checksums before publish")
    run = verify["run"]
    assert "set -euo pipefail" in run
    assert "test -f release-assets/release_note.md" in run
    assert "sha256sum -c SHA256SUMS.txt" in run


def test_job2_downloads_verified_release_artifact_only(workflow: dict) -> None:
    steps = workflow["jobs"][JOB2_NAME]["steps"]
    download_steps = [s for s in steps if "download-artifact" in s.get("uses", "")]
    assert len(download_steps) == 1
    with_block = download_steps[0]["with"]
    assert with_block["name"] == (
        "finbert-gguf-release-${{ needs.train-export-gate.outputs.model_version }}-${{ github.run_id }}"
    )
    assert with_block["path"] == "release-assets"


def test_download_artifact_is_sha_pinned(workflow: dict) -> None:
    steps = workflow["jobs"][JOB2_NAME]["steps"]
    uses_list = [s["uses"] for s in steps if "uses" in s]
    download_uses = [u for u in uses_list if u.startswith("actions/download-artifact@")]
    assert len(download_uses) == 1
    ref = download_uses[0].split("@", 1)[1]
    # 对齐仓库既有锚定风格：完整 40 位 commit SHA（非 mutable tag）
    assert re.fullmatch(r"[0-9a-f]{40}", ref), f"download-artifact ref 非 40 位 SHA 锚定: {ref}"


def test_release_artifact_fileset_is_minimal(workflow: dict) -> None:
    job1 = workflow["jobs"][JOB1_NAME]
    upload_steps = [
        s for s in job1["steps"] if str(s.get("with", {}).get("name", "")).startswith("finbert-gguf-release-")
    ]
    assert len(upload_steps) == 1
    with_block = upload_steps[0]["with"]
    paths = sorted(p.strip() for p in with_block["path"].strip().splitlines())
    assert paths == [
        "stage_b_out/SHA256SUMS.txt",
        "stage_b_out/finbert2-news-f16.gguf",
        "stage_b_out/head.npz",
        "stage_b_out/release_note.md",
    ]
    # 文件缺失时上传即失败（fail-safe），不允许静默空 artifact 进入发布 job
    assert with_block["if-no-files-found"] == "error"


def test_gate_runs_before_release_artifact_upload_in_job1(workflow: dict) -> None:
    steps = workflow["jobs"][JOB1_NAME]["steps"]
    names = [s.get("name") for s in steps]
    gate_idx = names.index("Consistency gate (verify_finbert_gguf.py --head)")
    sums_idx = names.index("Generate SHA256SUMS.txt")
    note_idx = names.index("Generate release note")
    rel_idx = names.index("Upload release artifacts (for publish job)")
    assert gate_idx < sums_idx < note_idx < rel_idx


def test_debug_artifact_upload_kept_always(workflow: dict) -> None:
    job1 = workflow["jobs"][JOB1_NAME]
    debug_steps = [s for s in job1["steps"] if s.get("name") == "Upload artifacts (incl. gate-failure debugging)"]
    assert len(debug_steps) == 1
    assert debug_steps[0]["if"] == "always()"


def test_job1_has_no_release_publish_step(workflow: dict) -> None:
    # 防回归：发布写操作必须留在 job2，job1（只读权限）不得残留 gh release 写命令
    job1 = workflow["jobs"][JOB1_NAME]
    for step in job1["steps"]:
        run = step.get("run", "")
        assert "gh release create" not in run
        assert "gh release upload" not in run


def test_job2_does_not_execute_repo_code(workflow: dict) -> None:
    # 发布 job 不 checkout、不运行仓库脚本：仅 artifact 下载 + 复验 + gh 发布
    job2 = workflow["jobs"][JOB2_NAME]
    for step in job2["steps"]:
        assert not str(step.get("uses", "")).startswith("actions/checkout@")


def test_job2_has_no_stale_step_context_refs(workflow: dict) -> None:
    # 拆 job 后不得残留对 job1 step 上下文的引用（steps.validate_inputs 在
    # job2 不存在，GitHub Actions 运行时会直接报错）
    job2_raw = yaml.safe_dump(workflow["jobs"][JOB2_NAME], allow_unicode=True)
    assert "steps.validate_inputs" not in job2_raw


def test_publish_step_uses_job_outputs(workflow: dict) -> None:
    job2 = workflow["jobs"][JOB2_NAME]
    publish = next(s for s in job2["steps"] if s.get("name") == "Publish release (finbert-gguf-v*)")
    assert publish["env"]["RELEASE_TAG"] == "${{ needs.train-export-gate.outputs.release_tag }}"
    run = publish["run"]
    assert "needs.train-export-gate.outputs.model_version" in run
    assert "release-assets/finbert2-news-f16.gguf" in run
    assert "release-assets/SHA256SUMS.txt" in run
    assert "--notes-file release-assets/release_note.md" in run
    assert '--target "$GITHUB_SHA"' in run


def test_gh_token_env_preserved_in_both_jobs(workflow: dict) -> None:
    jobs = workflow["jobs"]
    assert jobs[JOB1_NAME]["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert jobs[JOB2_NAME]["env"]["GH_TOKEN"] == "${{ github.token }}"


def test_timeout_budgets_preserved(workflow: dict) -> None:
    jobs = workflow["jobs"]
    # 训练大头（1~3h）保留 360 分钟预算；发布 job 收紧到 10 分钟
    assert jobs[JOB1_NAME]["timeout-minutes"] == 360
    assert jobs[JOB2_NAME]["timeout-minutes"] == 10


def test_publish_step_rejects_existing_release_tag(workflow: dict) -> None:
    # FT-05：同 tag 重跑拒绝覆盖——model_version 为不可变发布标识。
    # Release（tag）已存在时必须显式失败（exit 1 + 指引提升 model_version），
    # 防止 --clobber 部分更新令 lineage（note ↔ 资产 digest ↔ 运行参数）错位。
    job2 = workflow["jobs"][JOB2_NAME]
    publish = next(s for s in job2["steps"] if s.get("name") == "Publish release (finbert-gguf-v*)")
    run = publish["run"]
    # tag 已存在检测入口（gh release view 命中即拒绝）
    assert "gh release view" in run
    # 拒绝语义：非零退出 + 明确错误指引（含中断恢复路径：人工删 Release 留痕）
    assert "exit 1" in run
    assert "拒绝覆盖" in run
    assert "提升 model_version" in run
    # gh release create 仅在 tag 不存在分支可达：view 检查与拒绝退出
    # 必须先于 create 出现（顺序即分支语义的静态表达）
    assert run.index("gh release view") < run.index("gh release create")
    assert run.index("exit 1") < run.index("gh release create")


def test_publish_step_has_no_clobber_partial_update(workflow: dict) -> None:
    # FT-05：不把 --clobber 部分更新（只换资产不换 note）当作重跑幂等——
    # job2 不得残留 gh release upload / --clobber 上传路径。
    # （yaml.safe_load 丢弃 YAML 注释后 dump，注释中的说明性 "--clobber"
    # 提及不计入；job1 语料下载的 gh release download --clobber 为本地
    # 临时目录覆盖下载，与 Release 资产发布无关，不在本断言范围。）
    job2 = workflow["jobs"][JOB2_NAME]
    job2_raw = yaml.safe_dump(job2, allow_unicode=True)
    assert "--clobber" not in job2_raw
    assert "gh release upload" not in job2_raw
    for step in job2["steps"]:
        run = step.get("run", "")
        assert "gh release upload" not in run
        assert "--clobber" not in run


def _extract_pip_install_specs(run: str) -> list[str]:
    """从 shell run 文本提取 ``pip install`` 命令的包规格 token 列表。

    - 行尾 ``\\`` 续行拼接为完整命令行后逐行扫描；
    - 仅收集 ``pip install`` 命令的参数（遇其他命令行边界即停）；
    - 剥除引号、跳过 ``--flag`` 与 URL（``--index-url`` 的取值），只留包规格。
    yaml.safe_load 已丢弃 YAML 注释，注释中的示例写法不会被误提取。
    """
    logical_lines: list[str] = []
    buffer = ""
    for raw in run.splitlines():
        stripped = raw.rstrip()
        if stripped.endswith("\\"):
            buffer += stripped[:-1] + " "
        else:
            buffer += raw
            logical_lines.append(buffer)
            buffer = ""
    if buffer:
        logical_lines.append(buffer)

    specs: list[str] = []
    in_install = False
    for line in logical_lines:
        tokens = line.split()
        if tokens and tokens[0] == "pip":
            in_install = len(tokens) > 1 and tokens[1] == "install"
            if in_install:
                tokens = tokens[2:]
        elif in_install and tokens:
            # 逻辑行 = 完整命令（续行已拼接）：离开 install 上下文后
            # 其余命令（如 cd）不得误收集为包规格
            in_install = False
            continue
        if not in_install:
            continue
        skip_next = False
        for tok in tokens:
            if skip_next:
                skip_next = False
                continue
            if tok.startswith("--"):
                # --index-url <url>：取值（非 flag 形态的下一 token）一并跳过
                skip_next = tok == "--index-url"
                continue
            if "://" in tok:
                continue
            tok = tok.strip("\"'")
            if tok:
                specs.append(tok)
    return specs


def test_training_dependencies_fully_pinned(workflow: dict) -> None:
    # FT-08：训练依赖全部精确 pin——范围约束（如 accelerate>=1.1.0）与 bare
    # 包名（如 numpy sentencepiece）会随 PyPI 新版本漂移，引入训练数值行为
    # 差异，使跨次训练 lineage 比较失真。fullmatch 天然拦截一切非 == 形态。
    job1 = workflow["jobs"][JOB1_NAME]
    install = next(s for s in job1["steps"] if s.get("name") == INSTALL_STEP_NAME)
    specs = _extract_pip_install_specs(install["run"])
    assert specs, "未从依赖安装步骤提取到任何 pip 包规格（提取逻辑与 workflow 结构失配）"
    for spec in specs:
        assert PINNED_SPEC_PATTERN.fullmatch(spec), f"依赖未精确 pin（禁止范围约束/bare 包名）: {spec!r}"
    pinned_names = {spec.split("==", 1)[0] for spec in specs}
    missing = PINNED_TRAINING_PACKAGES - pinned_names
    assert not missing, f"训练依赖缺 pin: {sorted(missing)}"


def test_release_note_records_runtime_lineage(workflow: dict) -> None:
    # FT-08：Release note 环境段——滚动 runner 基线的兜底凭证，逐 run 如实
    # 记录当日 runner 镜像 / Python / 依赖版本，跨次训练行为差异可归因。
    job1 = workflow["jobs"][JOB1_NAME]
    note = next(s for s in job1["steps"] if s.get("name") == NOTE_STEP_NAME)
    run = note["run"]
    # runner 镜像当日版本（ImageOS/ImageVersion 为 runner-images 预置环境变量，
    # 缺失时显性记录 unknown 而非静默丢失）
    assert 'os.environ.get("ImageOS"' in run
    assert 'os.environ.get("ImageVersion"' in run
    assert "**训练环境**" in run and "ImageVersion=" in run
    # Python 实际版本（platform.python_version() 等价 python --version 输出）
    assert "platform.python_version()" in run
    assert "**Python**:" in run
    # 依赖版本动态读取已安装 metadata（与 pip pin 单一事实源，防双份硬编码漂移）
    assert "importlib.metadata" in run
    assert "**依赖版本**:" in run
    # fail-fast：metadata 读取失败时拒绝发布（lineage 不完整的 note 不上 Release）
    assert "PackageNotFoundError" in run
    assert "拒绝发布" in run


def test_release_note_lineage_packages_match_pinned_dependencies(workflow: dict) -> None:
    # FT-08：note 生成脚本读取的包列表与 pip install pin 的包集合一致——
    # 拦截「pin 了但 note 没读 / note 读了但没 pin」的错配漂移。
    job1 = workflow["jobs"][JOB1_NAME]
    install = next(s for s in job1["steps"] if s.get("name") == INSTALL_STEP_NAME)
    specs = _extract_pip_install_specs(install["run"])
    pinned_names = {spec.split("==", 1)[0] for spec in specs}
    note = next(s for s in job1["steps"] if s.get("name") == NOTE_STEP_NAME)
    match = re.search(r"lineage_packages\s*=\s*\(([^)]*)\)", note["run"], re.DOTALL)
    assert match, "note 生成脚本缺少 lineage_packages 定义"
    note_names = set(re.findall(r'"([^"]+)"', match.group(1)))
    assert note_names, "lineage_packages 元组为空"
    assert note_names == pinned_names, (
        f"note lineage 包列表与 pip pin 集合错配: 仅 note 有 {sorted(note_names - pinned_names)}, "
        f"仅 pin 有 {sorted(pinned_names - note_names)}"
    )


def test_release_note_embedded_python_compiles(workflow: dict) -> None:
    # 防手误：note 生成 heredoc 内嵌 Python 代码必须可编译（yaml.safe_load 仅
    # 验证 YAML 结构，shell heredoc 内的 Python 语法错误无其他门禁拦截）。
    job1 = workflow["jobs"][JOB1_NAME]
    note = next(s for s in job1["steps"] if s.get("name") == NOTE_STEP_NAME)
    run = note["run"]
    start = run.index("python - <<'EOF'")
    start = run.index("\n", start) + 1
    end = run.index("\nEOF", start)
    embedded = run[start:end]
    compile(embedded, "finbert-train.yml:Generate release note heredoc", "exec")


def test_runner_baseline_is_ubuntu_latest(workflow: dict) -> None:
    # FT-08 基线决策：保持滚动 runner ubuntu-latest（固定 container 的权衡与
    # note 环境段兜底见 workflow 文件头注释）；改固定 container 须有意变更本断言。
    for job_name in (JOB1_NAME, JOB2_NAME):
        assert workflow["jobs"][job_name]["runs-on"] == "ubuntu-latest"


def test_llama_cpp_checkout_is_commit_anchored(workflow: dict) -> None:
    # FT-08：llama.cpp 工具链必须 checkout 不可变 commit（tag v0.3.0 指向的提交），
    # 而非可变 tag——tag 被上游移动/重打时，仅记录 tag 无法定位实际拉取的提交。
    job1 = workflow["jobs"][JOB1_NAME]
    checkout = next(s for s in job1["steps"] if s.get("name") == LLAMA_CPP_CHECKOUT_STEP)
    with_block = checkout["with"]
    assert with_block["repository"] == "ggml-org/llama.cpp"
    ref = with_block["ref"]
    assert re.fullmatch(r"[0-9a-f]{40}", ref), f"llama.cpp ref 非 40 位 commit 锚定: {ref!r}"
    assert ref == LLAMA_CPP_COMMIT


def test_release_note_records_anchored_llama_cpp_commit(workflow: dict) -> None:
    # FT-08：note lineage 的 llama.cpp commit 必须逐字等于 checkout 锚定的 ref
    # （单一事实源，拦截「ref 改了但 note 未同步」的双份记录漂移）；同时保留 tag
    # 别名，便于与上游 release note 对照。
    job1 = workflow["jobs"][JOB1_NAME]
    checkout = next(s for s in job1["steps"] if s.get("name") == LLAMA_CPP_CHECKOUT_STEP)
    note = next(s for s in job1["steps"] if s.get("name") == NOTE_STEP_NAME)["run"]
    assert "**llama.cpp 工具链**" in note
    assert checkout["with"]["ref"] in note, "Release note lineage 未记录实际锚定的 llama.cpp commit"
    assert LLAMA_CPP_COMMIT in note
    assert "v0.3.0" in note
