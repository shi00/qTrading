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
  Python 版本、显式 pin 依赖版本（动态读取已安装 metadata，与 pip pin 单一
  事实源）、已安装环境**全量依赖清单 + SHA256 摘要**（覆盖显式 pin 之外的
  传递依赖，避免「pin 住 6 个包、传递依赖静默漂移」的归因盲区）、
  llama.cpp 工具链 commit（与 checkout ref 单一事实源）；
- llama.cpp 工具链由 tag 改为不可变 commit 锚定（tag v0.3.0 指向的提交——
  tag 可被上游移动/重打，仅记录 tag 无法定位实际拉取的提交）；
- runner 基线为滚动 ``ubuntu-latest``（权衡与兜底见 workflow 文件头注释），
  改固定 container 须有意变更本断言。

模型效果硬门槛（FT-02）：
- job1 内在一致性 Gate 之后、Release note 之前运行
  ``scripts/verify_finbert_effect_gate.py``：新模型 ``test macro-F1`` 与 ``dev
  macro-F1`` 均须 ``>= 最近一次已发布 Release（tag finbert-gguf-v*，semver 取最新）
  的同名字段 - 0.03``，否则该 step 非零退出 → job1 失败 → publish-release 跳过
  → Release 不产生；
- 锚点数值**运行期从锚点 Release note 解析**，脚本 / workflow 不得硬编码历史数值；
- 容差 0.03 出处（``reviews/新闻达标模型微调.md`` §5.2「3 个百分点」判据）与「允许的
  噪声回退上限」语义须在 workflow 注释与脚本内写明；
- 锚点不可得三态（R21）：note 指标缺失/不可解析或锚点查询失败 → FAIL（fail-closed）；
  确无历史 Release → 不阻断（NO_BASELINE，须显式声明未施加门槛）；本次自身指标缺失/
  NaN/非有限 → FAIL。

全部为 workflow YAML 的静态断言，不触发任何训练 / 发布副作用。
"""

import importlib.util
import re
import sys
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
    # 显式 pin 依赖版本动态读取已安装 metadata（与 pip pin 单一事实源，防双份硬编码漂移）
    assert "importlib.metadata" in run
    assert "**显式 pin 训练依赖**:" in run
    # fail-fast：metadata 读取失败时拒绝发布（lineage 不完整的 note 不上 Release）
    assert "PackageNotFoundError" in run
    assert "拒绝发布" in run


def test_release_note_records_full_dependency_inventory(workflow: dict) -> None:
    # FT-08 对抗性检视补强：只记录 6 个显式 pin 会留下「传递依赖静默漂移」盲区
    # （tokenizers / safetensors / huggingface-hub / gguf-py 等随 PyPI 解析与本地路径
    # 安装变化，无可归因记录）。清单须取自已安装环境实况
    # （importlib.metadata.distributions()，不维护第二份手写列表），排序后拼接为规范
    # 文本并给出 SHA256 摘要，供跨次训练直接比对。
    job1 = workflow["jobs"][JOB1_NAME]
    run = next(s for s in job1["steps"] if s.get("name") == NOTE_STEP_NAME)["run"]
    assert "from importlib.metadata import distributions" in run
    assert "for dist in distributions():" in run
    assert "env_inventory.sort(key=str.lower)" in run
    assert "hashlib.sha256" in run
    # note 文本含：条目数 + 摘要 + 可展开的全量清单（全部依赖版本写入 lineage）
    assert "**完整依赖清单**" in run
    assert "len(env_inventory)" in run
    assert "env_inventory_digest" in run
    assert "env_inventory_text" in run
    assert "展开完整依赖清单" in run


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


# --------------------------------------------------------------------------- #
# FT-02：模型效果硬门槛（test/dev macro-F1 与最近一次已发布 Release 比较）
# --------------------------------------------------------------------------- #
EFFECT_GATE_STEP_NAME = "Effect gate (verify_finbert_effect_gate.py)"
CONSISTENCY_GATE_STEP_NAME = "Consistency gate (verify_finbert_gguf.py --head)"
NOTE_STEP_NAME_FT02 = "Generate release note"
EFFECT_GATE_SCRIPT = REPO_ROOT / "scripts" / "verify_finbert_effect_gate.py"
# 历史锚点实测值（0.6711637549403506 / 0.7903670121227373）及其截断形态——锚点必须在
# 运行期从 Release note 解析，出现任一形态即视为硬编码（与未来 Release 漂移）。
FORBIDDEN_HARDCODED_BASELINES = (
    "0.7903670121227373",
    "0.6711637549403506",
    "0.7904",
    "0.6712",
    "0.7903",
    "0.6711",
)
TOLERANCE = 0.03


@pytest.fixture(scope="module")
def effect_gate_module():
    # 以文件路径加载脚本模块（不依赖 scripts 是否为包），供纯函数边界断言。
    spec = importlib.util.spec_from_file_location("verify_finbert_effect_gate", EFFECT_GATE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _effect_gate_step(workflow: dict) -> dict:
    return next(s for s in workflow["jobs"][JOB1_NAME]["steps"] if s.get("name") == EFFECT_GATE_STEP_NAME)


def _anchor(module, state: str, *, dev=None, test=None, tag: str | None = "finbert-gguf-v0.1.0"):
    return module.Anchor(state=state, tag=tag, dev_macro_f1=dev, test_macro_f1=test, detail="unit-test")


def test_effect_gate_runs_in_job1_before_note_and_release_upload(workflow: dict) -> None:
    names = [s.get("name") for s in workflow["jobs"][JOB1_NAME]["steps"]]
    consistency_idx = names.index(CONSISTENCY_GATE_STEP_NAME)
    effect_idx = names.index(EFFECT_GATE_STEP_NAME)
    sums_idx = names.index("Generate SHA256SUMS.txt")
    note_idx = names.index(NOTE_STEP_NAME_FT02)
    release_idx = names.index("Upload release artifacts (for publish job)")
    assert consistency_idx < effect_idx < sums_idx < note_idx < release_idx


def test_effect_gate_stays_in_readonly_job_absent_from_publish_job(workflow: dict) -> None:
    # 闸门必须落在 job1 的失败判定链上（job1 只读权限、job2 才写）：FAIL → job1 非
    # success → publish-release 跳过 → Release 不产生。放进 job2 会绕过 job1 判定。
    jobs = workflow["jobs"]
    assert jobs[JOB1_NAME]["permissions"] == {"contents": "read"}
    assert any(s.get("name") == EFFECT_GATE_STEP_NAME for s in jobs[JOB1_NAME]["steps"])
    job2_raw = yaml.safe_dump(jobs[JOB2_NAME], allow_unicode=True)
    assert EFFECT_GATE_STEP_NAME not in job2_raw
    assert "verify_finbert_effect_gate.py" not in job2_raw
    # job2 仍由 needs + if success 与 job1 联动（闸门失败时不可能被执行）
    assert jobs[JOB2_NAME]["needs"] == [JOB1_NAME]
    assert jobs[JOB2_NAME]["if"] == f"needs.{JOB1_NAME}.result == 'success'"


def test_effect_gate_step_cannot_silently_swallow_failure(workflow: dict) -> None:
    # 绕过面 1：continue-on-error → 失败被吞，job1 仍 success 并放行发布；
    # 绕过面 2：条件跳过（if）→ 闸门未跑也算通过。
    job1 = workflow["jobs"][JOB1_NAME]
    step = _effect_gate_step(workflow)
    assert "continue-on-error" not in step
    assert "if" not in step
    run = step["run"]
    assert "set -euo pipefail" in run
    assert "scripts/verify_finbert_effect_gate.py" in run
    assert "--train-summary stage_b_out/best/train_summary.json" in run
    assert '--repo "$EFFECT_GATE_REPO"' in run
    # 上下文经 env 注入（表达式注入面收敛）：run 内不得直接内插 ${{ ... }}
    assert step["env"]["EFFECT_GATE_REPO"] == "${{ github.repository }}"
    assert "${{" not in run
    # gh 鉴权依赖：job1 级 env 必须提供 GH_TOKEN（缺失时 gh 报错 → 闸门恒 fail-closed，
    # 发布被永久阻断）；对齐 job2 同款 `github.token`（只读足够列/读 Release）。
    assert job1["env"]["GH_TOKEN"] == "${{ github.token }}"


def test_effect_gate_no_hardcoded_baseline_values(workflow: dict) -> None:
    # 锚点必须运行期从 Release note 解析：workflow 全文（含注释）与脚本均不得出现
    # 历史锚点数值——硬编码会与未来 Release 漂移（脚本内 TOLERANCE=0.03 不在此列）。
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    script = EFFECT_GATE_SCRIPT.read_text(encoding="utf-8")
    for forbidden in FORBIDDEN_HARDCODED_BASELINES:
        assert forbidden not in workflow_text, f"workflow 硬编码了锚点数值 {forbidden}"
        assert forbidden not in script, f"脚本硬编码了锚点数值 {forbidden}"


def test_effect_gate_tolerance_and_source_documented(workflow: dict) -> None:
    # 要求「在 workflow 注释与脚本内写明」：注释不进 yaml.safe_load 的 run 值，
    # 故按 workflow 文件全文（含注释）断言。
    for text, label in (
        (WORKFLOW_PATH.read_text(encoding="utf-8"), "workflow 全文"),
        (EFFECT_GATE_SCRIPT.read_text(encoding="utf-8"), "脚本"),
    ):
        assert str(TOLERANCE) in text, f"{label} 未写明门槛容差 0.03"
        assert "3 个百分点" in text, f"{label} 未写明容差出处（3 个百分点）"
        assert "§5.2" in text and "reviews/新闻达标模型微调.md" in text, f"{label} 未写明容差出处路径"
        assert "噪声回退" in text, f"{label} 未写明容差语义（允许的噪声回退上限）"


def test_effect_gate_report_uploaded_even_on_failure(workflow: dict) -> None:
    debug = next(
        s
        for s in workflow["jobs"][JOB1_NAME]["steps"]
        if s.get("name") == "Upload artifacts (incl. gate-failure debugging)"
    )
    paths = [p.strip() for p in debug["with"]["path"].strip().splitlines()]
    assert "stage_b_out/effect_gate.json" in paths


def test_release_note_records_effect_gate_lineage(workflow: dict) -> None:
    # lineage 补全：门槛口径（公式 + 容差出处）、锚点 tag、锚点 dev/test、本次
    # dev/test、判定结论（PASS / NO_BASELINE）须全部落入 Release note。
    run = next(s for s in workflow["jobs"][JOB1_NAME]["steps"] if s.get("name") == NOTE_STEP_NAME_FT02)["run"]
    assert "stage_b_out/effect_gate.json" in run
    # 缺失即 fail-fast：不发布 lineage 不完整的 note（FAIL 态本就在 Effect gate 步骤
    # 阻断，note 步骤不可达）
    assert "effect_gate.json 缺失" in run
    for token in (
        "## 效果门槛（FT-02）",
        "anchor_tag",
        "baseline_dev_macro_f1",
        "baseline_test_macro_f1",
        "current_dev_macro_f1",
        "current_test_macro_f1",
        "tolerance",
        "tolerance_source",
        "NO_BASELINE",
        "未施加效果门槛",
    ):
        assert token in run, f"Release note 效果门槛 lineage 缺字段/措辞: {token}"


def test_parse_semver_tag_accepts_model_release_shape(effect_gate_module) -> None:
    assert effect_gate_module.parse_semver_tag("finbert-gguf-v0.1.0") == (0, 1, 0)
    assert effect_gate_module.parse_semver_tag("finbert-gguf-v1.10.2") == (1, 10, 2)


def test_parse_semver_tag_rejects_non_model_tags(effect_gate_module) -> None:
    module = effect_gate_module
    for tag in (
        "corpus-v1",
        "finbert-gguf-v1",
        "finbert-gguf-v1.2",
        "finbert-gguf-v1.2.3-rc1",
        "v0.1.0",
        "finbert-gguf-v0.1.0 ",
    ):
        assert module.parse_semver_tag(tag) is None, tag


def test_select_latest_release_tag_orders_by_semver_not_lexicographically(effect_gate_module) -> None:
    # 对抗面：1.10.0 词法上小于 1.9.0、语义上更大——按字符串排序会选错锚点。
    module = effect_gate_module
    tags = ["finbert-gguf-v1.9.0", "finbert-gguf-v1.10.0", "corpus-v1", "finbert-gguf-v1.2.3"]
    assert module.select_latest_release_tag(tags) == "finbert-gguf-v1.10.0"


def test_select_latest_release_tag_returns_none_without_model_release(effect_gate_module) -> None:
    assert effect_gate_module.select_latest_release_tag(["corpus-v1", "docs-v2"]) is None


def test_parse_macro_f1_from_note_reads_metrics_line(effect_gate_module) -> None:
    note = (
        "# FinBERT GGUF 模型 finbert-gguf-v0.1.0\n"
        "\n"
        "- **model_version**: 0.1.0\n"
        "- **dev macro-F1**: 0.6711637549403506 / **test macro-F1**: 0.7903670121227373\n"
    )
    assert effect_gate_module.parse_macro_f1_from_note(note) == (0.6711637549403506, 0.7903670121227373)


def test_parse_macro_f1_from_note_ignores_effect_gate_lineage_lines(effect_gate_module) -> None:
    # 对抗面：「格式合法但语义错误」的 note——lineage 效果门槛段同样含 dev/test 数值，
    # 措辞为「锚点 dev/test macro-F1」/「本次 dev/test macro-F1」；若解析器按子串匹配，
    # 会命中这些行（甚至读回锚点自身数值使门槛恒等通过）。整行锚定即可排除。
    module = effect_gate_module
    note = (
        "- **dev macro-F1**: 0.5 / **test macro-F1**: 0.6\n"
        "- **锚点 dev/test macro-F1**: 0.5 / 0.6\n"
        "- **本次 dev/test macro-F1**: 0.9 / 0.9\n"
    )
    assert module.parse_macro_f1_from_note(note) == (0.5, 0.6)


def test_parse_macro_f1_from_note_missing_or_non_finite(effect_gate_module) -> None:
    module = effect_gate_module
    assert module.parse_macro_f1_from_note("- **model_version**: 0.1.0\n") == (None, None)
    assert module.parse_macro_f1_from_note("- **dev macro-F1**: 0.7 / **test macro-F1**: NaN\n") == (None, None)
    assert module.parse_macro_f1_from_note("") == (None, None)


def test_finite_or_none_normalizes_non_finite_and_non_numeric(effect_gate_module) -> None:
    # 指标归一入口：缺失 / 字符串 / bool / NaN / Inf 一律 None（= FAIL），
    # 只有有限数值才可用于门槛比较（R21：缺失不得伪装成合法分值）。
    module = effect_gate_module
    assert module._finite_or_none(0.9) == 0.9
    assert module._finite_or_none(1) == 1.0
    for value in (None, "0.9", True, float("nan"), float("inf"), float("-inf")):
        assert module._finite_or_none(value) is None, value


def test_evaluate_effect_gate_pass_and_tolerance_boundary(effect_gate_module) -> None:
    module = effect_gate_module
    anchor = _anchor(module, "available", dev=0.80, test=0.70)
    # 恰好等于下限（>= 语义，边界含容差末端）→ PASS
    report = module.evaluate_effect_gate(current_dev=0.77, current_test=0.67, anchor=anchor)
    assert report["verdict"] == "PASS"
    assert report["criteria"]["test_macro_f1"]["floor"] == pytest.approx(0.67)
    assert report["criteria"]["dev_macro_f1"]["floor"] == pytest.approx(0.77)


def test_evaluate_effect_gate_fails_below_tolerance(effect_gate_module) -> None:
    module = effect_gate_module
    anchor = _anchor(module, "available", dev=0.80, test=0.70)
    test_regression = module.evaluate_effect_gate(current_dev=0.77, current_test=0.6699, anchor=anchor)
    assert test_regression["verdict"] == "FAIL"
    assert test_regression["criteria"]["test_macro_f1"]["ok"] is False
    dev_regression = module.evaluate_effect_gate(current_dev=0.7699, current_test=0.67, anchor=anchor)
    assert dev_regression["verdict"] == "FAIL"
    assert dev_regression["criteria"]["dev_macro_f1"]["ok"] is False


def test_evaluate_effect_gate_no_baseline_does_not_block_but_declares(effect_gate_module) -> None:
    module = effect_gate_module
    report = module.evaluate_effect_gate(
        current_dev=0.1, current_test=0.1, anchor=_anchor(module, "no_baseline", tag=None)
    )
    assert report["verdict"] == "NO_BASELINE"
    assert report["anchor_tag"] is None
    assert any("无历史 Release" in reason for reason in report["reasons"])


def test_evaluate_effect_gate_unreadable_anchor_is_fail_closed(effect_gate_module) -> None:
    # R21：存在历史 Release 但 note 指标缺失/不可解析（或锚点查询失败）→ FAIL，
    # 不得当成「已达标」放行；即便本次指标看起来很好。
    module = effect_gate_module
    report = module.evaluate_effect_gate(
        current_dev=0.99, current_test=0.99, anchor=_anchor(module, "unreadable", tag="finbert-gguf-v0.1.0")
    )
    assert report["verdict"] == "FAIL"


def test_evaluate_effect_gate_current_metrics_missing_or_non_finite_is_fail(effect_gate_module) -> None:
    module = effect_gate_module
    anchor = _anchor(module, "available", dev=0.8, test=0.7)
    assert module.evaluate_effect_gate(current_dev=None, current_test=0.7, anchor=anchor)["verdict"] == "FAIL"
    assert module.evaluate_effect_gate(current_dev=0.8, current_test=None, anchor=anchor)["verdict"] == "FAIL"
    assert module.evaluate_effect_gate(current_dev=float("nan"), current_test=0.7, anchor=anchor)["verdict"] == "FAIL"
    assert module.evaluate_effect_gate(current_dev=0.8, current_test=float("inf"), anchor=anchor)["verdict"] == "FAIL"
    # 无基线 + 本次指标缺失 → 仍 FAIL（不得用「无基线」掩盖不可用指标）
    assert (
        module.evaluate_effect_gate(
            current_dev=None, current_test=None, anchor=_anchor(module, "no_baseline", tag=None)
        )["verdict"]
        == "FAIL"
    )


def test_main_exit_code_semantics(monkeypatch, tmp_path, effect_gate_module) -> None:
    # 退出码契约：0 = PASS / NO_BASELINE（可继续发布）；1 = FAIL（阻断发布）。
    module = effect_gate_module
    summary = tmp_path / "train_summary.json"
    summary.write_text('{"best_dev_macro_f1": 0.9, "test_macro_f1": 0.9}', encoding="utf-8")
    base_args = ["--train-summary", str(summary), "--repo", "owner/repo"]

    monkeypatch.setattr(
        module,
        "resolve_anchor",
        lambda repo: _anchor(module, "no_baseline", tag=None),
    )
    report_path = tmp_path / "effect_gate.json"
    assert module.main([*base_args, "--report", str(report_path)]) == 0
    assert report_path.is_file()

    monkeypatch.setattr(
        module,
        "resolve_anchor",
        lambda repo: _anchor(module, "unreadable", tag="finbert-gguf-v0.1.0"),
    )
    assert module.main([*base_args, "--report", str(report_path)]) == 1


def test_resolve_anchor_is_fail_closed_when_gh_unavailable(monkeypatch, effect_gate_module) -> None:
    # 断网 / gh 不可用：无法确认「是否存在历史 Release」时不得 fail-open 放行。
    module = effect_gate_module

    def _boom(args):
        raise module.EffectGateError("gh 调用失败（exit 127）")

    monkeypatch.setattr(module, "_run_gh", _boom)
    anchor = module.resolve_anchor("owner/repo")
    assert anchor.state == "unreadable"
    assert anchor.dev_macro_f1 is None and anchor.test_macro_f1 is None


def test_resolve_anchor_reads_metrics_from_anchor_release_note(monkeypatch, effect_gate_module) -> None:
    module = effect_gate_module

    def _fake_gh(args):
        if args[:2] == ["release", "list"]:
            return '[{"tagName": "corpus-v1"}, {"tagName": "finbert-gguf-v0.1.0"}]'
        assert args[:2] == ["release", "view"]
        assert args[2] == "finbert-gguf-v0.1.0"
        return "- **dev macro-F1**: 0.6711637549403506 / **test macro-F1**: 0.7903670121227373\n"

    monkeypatch.setattr(module, "_run_gh", _fake_gh)
    anchor = module.resolve_anchor("owner/repo")
    assert anchor.state == "available"
    assert anchor.tag == "finbert-gguf-v0.1.0"
    assert anchor.dev_macro_f1 == 0.6711637549403506
    assert anchor.test_macro_f1 == 0.7903670121227373


def test_resolve_anchor_missing_metrics_in_note_is_fail_closed(monkeypatch, effect_gate_module) -> None:
    # 存在历史 Release 但 note 无指标（如旧版 note）→ unreadable，不得放行。
    module = effect_gate_module

    def _fake_gh(args):
        if args[:2] == ["release", "list"]:
            return '[{"tagName": "finbert-gguf-v0.1.0"}]'
        return "# FinBERT GGUF 模型 finbert-gguf-v0.1.0\n\n- **model_version**: 0.1.0\n"

    monkeypatch.setattr(module, "_run_gh", _fake_gh)
    anchor = module.resolve_anchor("owner/repo")
    assert anchor.state == "unreadable"
    assert anchor.tag == "finbert-gguf-v0.1.0"


# --------------------------------------------------------------------------- #
# FT-07：corpus 多版本 manifest 校验（行数/digest 期望值取自语料 manifest）
# --------------------------------------------------------------------------- #
CORPUS_STEP_NAME = "Download training corpus (Release) and verify integrity"
CORPUS_MANIFEST_SCRIPT = REPO_ROOT / "scripts" / "corpus_manifest.py"
CORPUS_MANIFEST_REPORT = "stage_b_out/corpus_manifest_report.json"
# 原硬编码语料行数（8813 + 1102 + 1102 = 11017，corpus-v1 口径）。FT-07 后这些期望值须取自
# 语料 manifest——workflow 全文（含注释）不得再出现，否则换用不同样本量的语料版本即被旧断言
# 拒绝（正是本问题根因）。
FORBIDDEN_HARDCODED_CORPUS_COUNTS = ("8813", "11017", "1102")


def _corpus_step(workflow: dict) -> dict:
    return next(s for s in workflow["jobs"][JOB1_NAME]["steps"] if s.get("name") == CORPUS_STEP_NAME)


def test_corpus_step_uses_manifest_verifier(workflow: dict) -> None:
    # 语料校验须交给 scripts/corpus_manifest.py（读 manifest 期望值），而非 workflow 内联断言。
    run = _corpus_step(workflow)["run"]
    assert 'gh release download "$CORPUS_TAG"' in run
    assert "sha256sum -c SHA256SUMS.txt" in run
    assert "scripts/corpus_manifest.py verify" in run
    assert "--corpus-dir data/corpus" in run
    assert '--corpus-tag "$CORPUS_TAG"' in run
    assert CORPUS_MANIFEST_REPORT in run


def test_corpus_step_no_hardcoded_counts_anywhere_in_workflow(workflow: dict) -> None:
    # 反根因：workflow 全文（含注释）不得再出现硬编码语料行数（与 FT-02 锚点同款口径）。
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    for forbidden in FORBIDDEN_HARDCODED_CORPUS_COUNTS:
        assert not re.search(rf"\b{re.escape(forbidden)}\b", workflow_text), (
            f"workflow 仍硬编码了语料行数 {forbidden}——期望值须取自语料 manifest"
        )


def test_corpus_step_cannot_silently_swallow_failure(workflow: dict) -> None:
    # 绕过面：continue-on-error 吞失败 / if 条件跳过（校验未跑也算通过）。
    step = _corpus_step(workflow)
    assert "continue-on-error" not in step
    assert "if" not in step
    run = step["run"]
    assert "set -euo pipefail" in run
    # 上下文经 env 注入（表达式注入面收敛）：run 内不得直接内插 ${{ ... }}
    assert "${{" not in run
    assert step["env"]["CORPUS_TAG"] == "${{ steps.validate_inputs.outputs.corpus_tag }}"


def test_release_note_reads_corpus_manifest_report_and_declares_state(workflow: dict) -> None:
    # lineage 单一事实源：note 的行数/digest 取自 manifest 校验报告，不再自行读 SHA256SUMS.txt；
    # 缺失即 fail-fast；legacy 语料须显式声明「未做 manifest 校验」（R21）。
    steps = workflow["jobs"][JOB1_NAME]["steps"]
    names = [s.get("name") for s in steps]
    # 语料校验须先于 note 生成（报告为 note 的行数/digest 唯一事实源）。
    assert names.index(CORPUS_STEP_NAME) < names.index(NOTE_STEP_NAME)
    run = next(s for s in steps if s.get("name") == NOTE_STEP_NAME)["run"]
    assert CORPUS_MANIFEST_REPORT in run
    assert "corpus_manifest_report.json 缺失" in run
    assert "manifest_state" in run
    assert "manifest_schema" in run
    assert "manifest_version" in run
    assert "未做 manifest 校验" in run
    assert "新增语料版本必须随附 corpus_manifest.json" in run
    # note 生成脚本不得再出现硬编码行数（唯一事实源为报告）。
    for forbidden in FORBIDDEN_HARDCODED_CORPUS_COUNTS:
        assert not re.search(rf"\b{re.escape(forbidden)}\b", run), f"note 硬编码了语料行数 {forbidden}"


def test_corpus_manifest_report_uploaded_for_debug(workflow: dict) -> None:
    # 校验报告须落入调试 artifact（失败时仍可归因：缺 manifest / 行数不符 / 漂移）。
    debug = next(
        s
        for s in workflow["jobs"][JOB1_NAME]["steps"]
        if s.get("name") == "Upload artifacts (incl. gate-failure debugging)"
    )
    paths = [p.strip() for p in debug["with"]["path"].strip().splitlines()]
    assert CORPUS_MANIFEST_REPORT in paths


def test_corpus_manifest_script_explicitly_labels_legacy_registry() -> None:
    # legacy 兜底必须是显式登记 + 显式声明（不得静默回退硬编码，R21）。
    script = CORPUS_MANIFEST_SCRIPT.read_text(encoding="utf-8")
    assert "LEGACY_CORPUS_REGISTRY" in script
    assert "未做 manifest 校验" in script
    assert "拒绝静默沿用硬编码期望值" in script
    # 未登记 tag 的修复指引必须给出（生成 manifest 的路径）。
    assert "corpus_manifest.py build" in script
    # dev 候选名防错位（train_finbert.DEV_CANDIDATES 首选 split_dev.jsonl）不得被删——
    # 混入该文件时校验对象与训练输入错位。
    assert "split_dev.jsonl" in script
    # schema 不兼容须拒绝（不按未知语义静默校验）。
    assert "schema 不兼容" in script


def test_corpus_step_tag_whitelist_still_guards_dispatch_input(workflow: dict) -> None:
    # corpus_tag 仍为 dispatch 输入，须经 Validate dispatch inputs 白名单校验后才进入下载/校验。
    validate_name = "Validate dispatch inputs (model_version semver, anti tag-injection)"
    steps = workflow["jobs"][JOB1_NAME]["steps"]
    names = [s.get("name") for s in steps]
    assert names.index(validate_name) < names.index(CORPUS_STEP_NAME)
    validate = next(s for s in steps if s.get("name") == validate_name)
    assert "corpus_tag" in validate["run"]
