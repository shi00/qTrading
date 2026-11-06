#!/usr/bin/env python3
"""测试用假 ``gh``：只处理 ``gh api``，读写临时“世界”文件并记录每次调用。

- ``SIDECAR_FAKE_GH_WORLD``：JSON 世界（releases / tag ref / 故障注入）。
- ``SIDECAR_FAKE_GH_CALLS``：JSONL 调用日志（argv、method、path、写标记、exit）。
- ``SIDECAR_FAKE_GH_WORLD`` 中的 ``failure`` 支持按 path 子串 + 第 N 次命中返回
  HTTP 500 或非 JSON 错误；``drop_asset_names`` / ``corrupt_digests`` /
  ``fail_upload_names`` 在资产 POST 时生效。

由 ``sidecar_release_harness`` 写入临时 PATH，调用方必须保证 PATH 优先命中本
脚本；本脚本不访问网络，也不读取真实 token。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

WORLD_ENV = "SIDECAR_FAKE_GH_WORLD"
CALLS_ENV = "SIDECAR_FAKE_GH_CALLS"


def _load_world() -> dict[str, Any]:
    with open(os.environ[WORLD_ENV], encoding="utf-8") as handle:
        return json.load(handle)


def _save_world(world: dict[str, Any]) -> None:
    with open(os.environ[WORLD_ENV], "w", encoding="utf-8") as handle:
        json.dump(world, handle, ensure_ascii=False, indent=2)


def _log_call(record: dict[str, Any]) -> None:
    with open(os.environ[CALLS_ENV], "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _emit(payload: Any, jq_expression: str | None) -> int:
    text = json.dumps(payload, ensure_ascii=False)
    if not jq_expression:
        sys.stdout.write(text + "\n")
        return 0
    result = subprocess.run(
        ["jq", "-r", jq_expression],
        input=text,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return 1
    sys.stdout.write(result.stdout)
    return 0


def _fail(message: str) -> int:
    sys.stderr.write(f"gh: {message}\n")
    return 1


def _typed(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered == "null":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _parse_api_args(args: list[str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {
        "method": None,
        "jq": None,
        "fields": {},
        "input_path": None,
        "header": None,
        "paginate": False,
        "path": None,
    }
    positional: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--paginate", "-i", "--include"}:
            parsed["paginate"] = arg == "--paginate"
            index += 1
        elif arg in {"--jq", "-q"}:
            parsed["jq"] = args[index + 1]
            index += 2
        elif arg in {"--method", "-X"}:
            parsed["method"] = args[index + 1]
            index += 2
        elif arg in {"-f", "--field", "--raw-field"}:
            key, _, value = args[index + 1].partition("=")
            parsed["fields"][key] = value
            index += 2
        elif arg in {"-F", "--typed-field"}:
            key, _, value = args[index + 1].partition("=")
            parsed["fields"][key] = _typed(value)
            index += 2
        elif arg in {"-H", "--header"}:
            parsed["header"] = args[index + 1]
            index += 2
        elif arg == "--input":
            parsed["input_path"] = args[index + 1]
            index += 2
        elif arg.startswith("-"):
            index += 1
        else:
            positional.append(arg)
            index += 1
    parsed["path"] = positional[0] if positional else None
    if parsed["method"] is None:
        parsed["method"] = "POST" if parsed["fields"] else "GET"
    else:
        parsed["method"] = str(parsed["method"]).upper()
    return parsed


def _failure_active(world: dict[str, Any], method: str, path: str) -> str | None:
    failure = world.get("failure")
    if not failure:
        return None
    expected_method = failure.get("method")
    if expected_method and expected_method != method:
        return None
    if failure.get("path_contains", "") not in path:
        return None
    matched = int(world.get("_failure_matches", 0)) + 1
    world["_failure_matches"] = matched
    if matched != failure.get("occurrence", 1):
        return None
    return str(failure.get("mode", "http500"))


def _find_release(world: dict[str, Any], release_id: int) -> dict[str, Any] | None:
    for release in world.get("releases", []):
        if release["id"] == release_id:
            return release
    return None


def _dispatch(world: dict[str, Any], parsed: dict[str, Any]) -> int:
    path = parsed["path"]
    method = parsed["method"]
    jq_expression = parsed["jq"]
    fields = parsed["fields"]
    if not path:
        return _fail("api path is required")

    failure_mode = _failure_active(world, method, path)
    if failure_mode == "http500":
        return _fail("HTTP 500: Internal Server Error")
    if failure_mode == "nonjson":
        sys.stdout.write("upstream 502 Bad Gateway\n")
        return _fail("unexpected response: response is not valid JSON")
    if failure_mode:
        return _fail(f"unknown failure mode {failure_mode}")

    base = path.split("?", 1)[0]

    tag_ref_match = re.search(r"/git/ref/tags/(?P<tag>[^/]+)$", base)
    if tag_ref_match and method == "GET":
        if tag_ref_match.group("tag") != world.get("tag"):
            return _fail("Not Found (HTTP 404)")
        return _emit(world["tag_ref"], jq_expression)

    tag_object_match = re.search(r"/git/tags/(?P<sha>[^/]+)$", base)
    if tag_object_match and method == "GET":
        tag_object = world.get("tag_object")
        if not tag_object or tag_object.get("sha") != tag_object_match.group("sha"):
            return _fail("Not Found (HTTP 404)")
        return _emit(tag_object, jq_expression)

    asset_delete_match = re.search(r"/releases/assets/(?P<asset_id>\d+)$", base)
    if asset_delete_match and method == "DELETE":
        asset_id = int(asset_delete_match.group("asset_id"))
        for release in world.get("releases", []):
            release["assets"] = [asset for asset in release["assets"] if asset["id"] != asset_id]
        return _emit({}, jq_expression)

    release_assets_match = re.search(r"/releases/(?P<release_id>\d+)/assets$", base)
    if release_assets_match:
        release = _find_release(world, int(release_assets_match.group("release_id")))
        if release is None:
            return _fail("Not Found (HTTP 404)")
        if method == "GET":
            return _emit(release["assets"], jq_expression)
        if method == "POST":
            # 真实 GitHub 不提供该主机上的资产上传；上传必须走 release.upload_url。
            return _fail("HTTP 404: release asset upload must use the release upload_url host")
        return _fail(f"HTTP 405: method {method} not allowed for assets")

    release_match = re.search(r"/releases/(?P<release_id>\d+)$", base)
    if release_match:
        release = _find_release(world, int(release_match.group("release_id")))
        if release is None:
            return _fail("Not Found (HTTP 404)")
        if method == "GET":
            return _emit(release, jq_expression)
        if method == "PATCH":
            release["draft"] = fields.get("draft", release["draft"])
            release["make_latest"] = fields.get("make_latest", release.get("make_latest"))
            return _emit(release, jq_expression)
        return _fail(f"HTTP 405: method {method} not allowed for release")

    if base.endswith("/releases"):
        if method == "GET":
            return _emit(world.get("releases", []), jq_expression)
        if method == "POST":
            release_id = int(world.get("_next_release_id", 100))
            world["_next_release_id"] = release_id + 1
            release = {
                "id": release_id,
                "tag_name": fields.get("tag_name", ""),
                "name": fields.get("name", ""),
                "draft": bool(fields.get("draft", False)),
                "prerelease": bool(fields.get("prerelease", False)),
                "assets": [],
                "upload_url": (
                    f"{world.get('upload_base', '')}/repos/{world.get('repo', '')}/releases/{release_id}/assets"
                    + ("{?name,label}" if world.get("upload_url_template", True) else "")
                ),
            }
            world.setdefault("releases", []).append(release)
            return _emit(release, jq_expression)
        return _fail(f"HTTP 405: method {method} not allowed for releases")

    return _fail(f"HTTP 404: no fake route for {method} {base}")


def _upload_binary_to_url(
    world: dict[str, Any],
    release: dict[str, Any],
    name: str,
    input_path: str,
    url: str,
    jq_expression: str | None,
) -> int:
    """模拟 gh 的资产传输层：向 Release upload_url 发送原始二进制并返回资产对象。

    真实 gh 在内部读取 release.upload_url 并访问资产上传主机；本 stub 用本地
    HTTP 服务替代该主机，使测试能断言实际主机、``name`` 查询参数与 body 字节。
    """
    with open(input_path, "rb") as handle:
        payload = handle.read()
    headers = {
        "Content-Type": "application/octet-stream",
        "User-Agent": "fake-gh-test",
    }
    token = os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
    except urllib.error.HTTPError as error:
        return _fail(f"HTTP {error.code}: {error.reason}")
    except urllib.error.URLError as error:
        return _fail(f"upload transport error: {error.reason}")

    digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    override = world.get("corrupt_digests", {}).get(name)
    if override:
        digest = override
    if name in set(world.get("drop_asset_names", [])):
        # 模拟服务端未落库：请求成功但最终资产集合缺少该文件。
        return _emit({"id": -1, "name": name, "digest": digest}, jq_expression)
    asset_id = int(world.get("_next_asset_id", 5000))
    world["_next_asset_id"] = asset_id + 1
    asset = {"id": asset_id, "name": name, "digest": digest}
    release["assets"].append(asset)
    return _emit(asset, jq_expression)


def _release_upload(world: dict[str, Any], args: list[str], jq_expression: str | None) -> int:
    """模拟 ``gh release upload <tag> <files>...``（含 draft 定位与 upload_url 传输）。"""
    if "--clobber" in args:
        return _fail("--clobber is forbidden in the sidecar publisher")
    positional: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--repo", "-R"}:
            index += 2
        elif arg.startswith("-"):
            index += 1
        else:
            positional.append(arg)
            index += 1
    if len(positional) < 2:
        return _fail("release upload requires a tag and at least one file")
    tag, file_paths = positional[0], positional[1:]

    releases = [release for release in world.get("releases", []) if release.get("tag_name") == tag]
    drafts = [release for release in releases if release.get("draft")]
    if drafts:
        release = drafts[0]
    elif releases:
        return _fail(f"release {tag} is not a draft; refusing to upload")
    else:
        return _fail(f"release {tag} not found (HTTP 404)")

    upload_url = str(release.get("upload_url") or "")
    upload_url = upload_url.split("{", 1)[0]
    if not urllib.parse.urlparse(upload_url).scheme:
        return _fail(f"release {tag} has no usable upload_url")

    for file_path in file_paths:
        name = os.path.basename(file_path)
        separator = "&" if "?" in upload_url else "?"
        url = f"{upload_url}{separator}name={urllib.parse.quote(name, safe='')}"
        status = _upload_binary_to_url(world, release, name, file_path, url, jq_expression)
        if status != 0:
            return status
    return 0


def main(argv: list[str]) -> int:
    record: dict[str, Any] = {
        "argv": list(argv),
        "subcommand": argv[0] if argv else "",
        "method": None,
        "path": None,
        "write": False,
        "fields": {},
        "exit": 70,
    }
    world = _load_world()
    try:
        if not argv:
            record["exit"] = _fail("unsupported fake gh invocation")
            return record["exit"]
        if argv[0] == "release" and len(argv) > 1 and argv[1] == "upload":
            record["subcommand"] = "release upload"
            record["method"] = "POST"
            record["write"] = True
            record["files"] = [arg for arg in argv[2:] if not arg.startswith("-")]
            record["exit"] = _release_upload(world, argv[2:], None)
            return record["exit"]
        if argv[0] != "api":
            record["exit"] = _fail(f"unsupported fake gh invocation: {' '.join(argv)}")
            return record["exit"]
        parsed = _parse_api_args(argv[1:])
        record.update(
            {
                "method": parsed["method"],
                "path": parsed["path"],
                "write": parsed["method"] in {"POST", "PATCH", "PUT", "DELETE"},
                "fields": parsed["fields"],
                "jq": parsed["jq"],
                "paginate": parsed["paginate"],
                "input_name": os.path.basename(parsed["input_path"]) if parsed["input_path"] else None,
            }
        )
        record["exit"] = _dispatch(world, parsed)
        return record["exit"]
    finally:
        _save_world(world)
        _log_call(record)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
