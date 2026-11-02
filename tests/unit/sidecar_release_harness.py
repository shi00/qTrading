"""D18-T01 模拟 gh CLI 的发布 workflow harness。

从 ``.github/workflows/sidecar.yml`` 的 ``publish-release`` 提取内联 shell，
替换 ``${{ ... }}`` 环境表达式后在临时目录用真实 ``bash`` 执行；PATH 首位放置
可执行的假 ``gh``（``tests/unit/fake_gh.py``），由它记录每次 API 调用并按临时
JSON 世界返回 Release / 资产对象。

设计边界：
- 只执行 publisher 的 5 个 ``run`` 步骤（下载 action 由测试直接铺好 12 个文件）；
- 不访问网络、不读取真实 token、不调用真实 gh（PATH 守卫在每次执行前校验）；
- GitHub Actions 的 ``needs`` 聚合无法在本地重放，由结构断言覆盖；
- publisher 真实运行在 ubuntu-latest，非 Linux 或缺少 bash/jq/GNU find 时用例跳过。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "sidecar.yml"
PUBLISH_JOB = "publish-release"

LOCAL_VERIFY_STEP = "Verify downloaded artifacts and tag commit"
STATE_STEP = "Check release state (refuse published; reuse draft)"
UPLOAD_STEP = "Create or reuse draft Release and upload 12 assets"
REMOTE_VERIFY_STEP = "Verify draft assets and SHA256 digests (gate before publish)"
PUBLISH_STEP = "Publish draft Release as latest (verification passed)"
STEP_ORDER = (LOCAL_VERIFY_STEP, STATE_STEP, UPLOAD_STEP, REMOTE_VERIFY_STEP, PUBLISH_STEP)

PLATFORMS = (
    ("windows", "x86_64-pc-windows-msvc", "qtrading-pg-sidecar-windows-x86_64.exe"),
    ("linux", "x86_64-unknown-linux-gnu", "qtrading-pg-sidecar-linux-x86_64"),
    ("linux-arm", "aarch64-unknown-linux-gnu", "qtrading-pg-sidecar-linux-aarch64"),
    ("macos", "aarch64-apple-darwin", "qtrading-pg-sidecar-macos-aarch64"),
)

EXPRESSION_RE = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
FAKE_TOKEN = "fake-test-token-not-a-secret"


@dataclass(frozen=True)
class StepSpec:
    name: str
    step_id: str
    run: str
    env: dict[str, str]


@dataclass
class StepResult:
    name: str
    exit_code: int
    stdout: str
    stderr: str
    outputs: dict[str, str] = field(default_factory=dict)


@dataclass
class ChainResult:
    executed: list[StepResult]

    @property
    def last(self) -> StepResult:
        return self.executed[-1]


def load_publish_steps() -> list[StepSpec]:
    """读取 publisher 的内联 shell 步骤；步骤集合变化时立即失败而非静默跳过。"""
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    job = workflow["jobs"][PUBLISH_JOB]
    specs: list[StepSpec] = []
    for step in job["steps"]:
        run = step.get("run")
        if run is None:
            continue
        specs.append(
            StepSpec(
                name=step["name"],
                step_id=step.get("id", ""),
                run=run,
                env=step.get("env") or {},
            )
        )
    if [spec.name for spec in specs] != list(STEP_ORDER):
        raise AssertionError(f"publisher 内联步骤与预期不一致: {[spec.name for spec in specs]}")
    return specs


def _job_env() -> dict[str, str]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return workflow["jobs"][PUBLISH_JOB].get("env") or {}


def make_asset(asset_id: int, name: str, digest: str | None = None) -> dict[str, Any]:
    return {"id": asset_id, "name": name, "digest": digest}


def make_release(
    release_id: int,
    tag: str,
    *,
    draft: bool,
    assets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": release_id,
        "tag_name": tag,
        "name": tag,
        "draft": draft,
        "assets": list(assets or []),
    }


class SidecarReleaseHarness:
    """单测试专用的临时发布世界 + 假 gh + 步骤执行器。"""

    def __init__(
        self,
        workdir: Path,
        *,
        tag: str = "sidecar-v1.2.3",
        commit_sha: str = "0" * 40,
        repo: str = "owner/qTrading",
        releases: list[dict[str, Any]] | None = None,
        annotated_tag: bool = False,
        tag_commit_sha: str | None = None,
        failure: dict[str, Any] | None = None,
        drop_asset_names: list[str] | None = None,
        corrupt_digests: dict[str, str] | None = None,
        fail_upload_names: list[str] | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.workdir = Path(workdir)
        self.tag = tag
        self.commit_sha = commit_sha
        self.repo = repo
        self.timeout_seconds = timeout_seconds

        self.bin_dir = self.workdir / "bin"
        self.run_dir = self.workdir / "run"
        self.steps_dir = self.workdir / "steps"
        self.assets_dir = self.run_dir / "release-assets"
        for directory in (self.bin_dir, self.run_dir, self.steps_dir, self.assets_dir):
            directory.mkdir(parents=True, exist_ok=True)

        self.world_path = self.workdir / "world.json"
        self.calls_path = self.workdir / "gh-calls.jsonl"
        self.calls_path.write_text("", encoding="utf-8")
        self._outputs: dict[str, dict[str, str]] = {}
        self._specs = load_publish_steps()

        self._build_world(
            releases=releases or [],
            annotated_tag=annotated_tag,
            tag_commit_sha=tag_commit_sha,
            failure=failure,
            drop_asset_names=drop_asset_names,
            corrupt_digests=corrupt_digests,
            fail_upload_names=fail_upload_names,
        )
        self._install_fake_gh()
        self._build_release_assets()

    # ------------------------------------------------------------------ setup
    def _build_world(
        self,
        *,
        releases: list[dict[str, Any]],
        annotated_tag: bool,
        tag_commit_sha: str | None,
        failure: dict[str, Any] | None,
        drop_asset_names: list[str] | None,
        corrupt_digests: dict[str, str] | None,
        fail_upload_names: list[str] | None,
    ) -> None:
        resolved_commit = tag_commit_sha or self.commit_sha
        world: dict[str, Any] = {
            "tag": self.tag,
            "releases": releases,
            "failure": failure,
            "drop_asset_names": drop_asset_names or [],
            "corrupt_digests": corrupt_digests or {},
            "fail_upload_names": fail_upload_names or [],
            "_next_release_id": 100,
            "_next_asset_id": 6000,
        }
        if annotated_tag:
            tag_object_sha = "b" * 40
            world["tag_ref"] = {"object": {"type": "tag", "sha": tag_object_sha}}
            world["tag_object"] = {
                "sha": tag_object_sha,
                "object": {"type": "commit", "sha": resolved_commit},
            }
        else:
            world["tag_ref"] = {"object": {"type": "commit", "sha": resolved_commit}}
        self.world_path.write_text(json.dumps(world, ensure_ascii=False, indent=2), encoding="utf-8")

    def _install_fake_gh(self) -> None:
        fake_module = Path(__file__).with_name("fake_gh.py")
        shim = self.bin_dir / "gh"
        shim.write_text(
            f'#!/usr/bin/env bash\nexec "{sys.executable}" "{fake_module}" "$@"\n',
            encoding="utf-8",
        )
        shim.chmod(0o755)

    def _build_release_assets(self) -> None:
        for os_name, target, artifact in PLATFORMS:
            directory = self.assets_dir / artifact
            directory.mkdir(parents=True, exist_ok=True)
            payload = f"payload:{artifact}:{target}".encode()
            (directory / artifact).write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            manifest = {
                "schema": "qtrading.sidecar.manifest.v1",
                "target": target,
                "git_sha": self.commit_sha[:12],
                "sha256": digest,
            }
            (directory / f"sidecar-manifest-{os_name}.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            (directory / f"sha256sums-{target}.txt").write_text(f"{digest}  {artifact}\n", encoding="utf-8")

    # ------------------------------------------------------------------ query
    @property
    def calls(self) -> list[dict[str, Any]]:
        if not self.calls_path.exists():
            return []
        return [json.loads(line) for line in self.calls_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def write_calls(self) -> list[dict[str, Any]]:
        return [call for call in self.calls if call.get("write")]

    def world(self) -> dict[str, Any]:
        return json.loads(self.world_path.read_text(encoding="utf-8"))

    @staticmethod
    def expected_asset_names() -> list[str]:
        names: list[str] = []
        for os_name, target, artifact in PLATFORMS:
            names.extend([artifact, f"sidecar-manifest-{os_name}.json", f"sha256sums-{target}.txt"])
        return names

    @property
    def steps(self) -> list[StepSpec]:
        return list(self._specs)

    def spec(self, name: str) -> StepSpec:
        for spec in self._specs:
            if spec.name == name:
                return spec
        raise KeyError(name)

    # --------------------------------------------------------------- execution
    def _resolve(self, expression: str) -> str:
        expression = expression.strip()
        if expression == "github.token":
            return FAKE_TOKEN
        if expression == "github.repository":
            return self.repo
        if expression == "github.ref_name":
            return self.tag
        if expression == "github.sha":
            return self.commit_sha
        match = re.fullmatch(r"steps\.([^.]+)\.outputs\.([^.]+)", expression)
        if match:
            step_id, key = match.groups()
            return self._outputs.get(step_id, {}).get(key, "")
        raise AssertionError(f"harness 无法解析 workflow 表达式: {expression!r}")

    def _interpolate(self, text: str) -> str:
        return EXPRESSION_RE.sub(lambda match: self._resolve(match.group(1)), text)

    def _step_env(self, spec: StepSpec, output_path: Path) -> dict[str, str]:
        env = {
            "PATH": f"{self.bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(self.workdir),
            "LANG": "C.UTF-8",
            "GITHUB_ACTIONS": "true",
            "GITHUB_SHA": self.commit_sha,
            "GITHUB_REF": f"refs/tags/{self.tag}",
            "GITHUB_REF_NAME": self.tag,
            "GITHUB_REPOSITORY": self.repo,
            "GITHUB_OUTPUT": str(output_path),
            "SIDECAR_FAKE_GH_WORLD": str(self.world_path),
            "SIDECAR_FAKE_GH_CALLS": str(self.calls_path),
        }
        for key, value in _job_env().items():
            env[key] = self._interpolate(value)
        for key, value in spec.env.items():
            env[key] = self._interpolate(value)
        resolved = shutil.which("gh", path=env["PATH"])
        if resolved is None or Path(resolved).resolve() != (self.bin_dir / "gh").resolve():
            raise AssertionError("PATH 未优先命中假 gh；拒绝执行以避免真实 API 调用")
        return env

    @staticmethod
    def _parse_outputs(path: Path) -> dict[str, str]:
        if not path.exists():
            return {}
        outputs: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator:
                outputs[key] = value
        return outputs

    def run_step(self, spec: StepSpec) -> StepResult:
        index = self._specs.index(spec)
        output_path = self.steps_dir / f"{index:02d}-{spec.step_id or 'step'}.output"
        output_path.write_text("", encoding="utf-8")
        env = self._step_env(spec, output_path)
        script = self._interpolate(spec.run)
        completed = subprocess.run(
            ["bash", "-c", script],
            cwd=self.run_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        outputs = self._parse_outputs(output_path)
        if spec.step_id:
            self._outputs[spec.step_id] = outputs
        return StepResult(
            name=spec.name,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            outputs=outputs,
        )

    def run_chain(self, steps: list[str] | None = None) -> ChainResult:
        """按 Actions 语义顺序执行，首个失败即停止后续步骤（模拟 job 中止）。"""
        selected = steps or list(STEP_ORDER)
        executed: list[StepResult] = []
        for name in selected:
            result = self.run_step(self.spec(name))
            executed.append(result)
            if result.exit_code != 0:
                break
        return ChainResult(executed)
