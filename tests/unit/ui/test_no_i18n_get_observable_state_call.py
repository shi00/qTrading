"""守护: UI 文件不得再调用 I18n.get_observable_state (已下沉到 ui.i18n.get_observable_state).

方案 A 后, Observable state 从 core/i18n.py 下沉到 ui/i18n.py,
调用点应从 ``ft.use_state(I18n.get_observable_state)`` 改为
``ft.use_state(get_observable_state)``. 本测试动态扫描 ui/ 和 main.py, 拦截回归.
"""

import pathlib
import re

import pytest

from ui.i18n import I18nState, get_observable_state

pytestmark = pytest.mark.unit

# 动态扫描 ui/ 和 main.py (不维护硬编码清单, 避免遗漏)
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SCAN_FILES = list((_PROJECT_ROOT / "ui").rglob("*.py")) + [_PROJECT_ROOT / "main.py"]


def test_no_i18n_get_observable_state_call():
    """UI 文件不得调用 I18n.get_observable_state (应改为 get_observable_state)."""
    pattern = re.compile(r"I18n\.get_observable_state")
    offenders = []
    for f in _SCAN_FILES:
        # ui/i18n.py 定义 get_observable_state, 不调用 I18n.get_observable_state, 跳过
        if f.name == "i18n.py" and f.parent.name == "ui":
            continue
        src = f.read_text(encoding="utf-8")
        for i, line in enumerate(src.splitlines(), 1):
            stripped = line.lstrip()
            # 排除注释行
            if stripped.startswith("#"):
                continue
            if pattern.search(line):
                offenders.append(f"{f.relative_to(_PROJECT_ROOT)}:{i}: {line.strip()}")
    assert not offenders, "仍存在 I18n.get_observable_state 调用 (应改为 get_observable_state):\n" + "\n".join(
        offenders
    )


def test_ui_files_import_get_observable_state():
    """调用 bare ``get_observable_state()`` (非 ``X.get_observable_state`` 方法调用) 的 UI 文件必须 import 它.

    注意: ``AppColors.get_observable_state`` 是 ``AppColors`` 类的 classmethod,
    属于不同作用域, 不需要 ``from ui.i18n import``. 本测试仅匹配**裸调用**
    (即 ``get_observable_state`` 不被 ``.`` 前缀, 表示已通过 ``from ui.i18n import`` 引入).
    同时排除 ``def get_observable_state`` 方法定义行 (其他类可定义同名方法).
    """
    # 裸调用: get_observable_state 不被 . 或 word char 前缀 (排除 X.get_observable_state)
    call_pattern = re.compile(r"(?<![\w.])get_observable_state\b")
    # 方法定义: def get_observable_state (排除其他类的同名方法定义)
    def_pattern = re.compile(r"\bdef\s+get_observable_state\b")
    import_pattern = re.compile(r"from ui\.i18n import.*get_observable_state")

    missing_import = []
    for f in _SCAN_FILES:
        if f.name == "i18n.py" and f.parent.name == "ui":
            continue
        src = f.read_text(encoding="utf-8")
        # 有调用但无 import
        has_call = False
        for line in src.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            # 排除方法定义行 (如 AppColors.get_observable_state 的 def)
            if def_pattern.search(line):
                continue
            if call_pattern.search(line):
                has_call = True
                break
        if has_call and not import_pattern.search(src):
            missing_import.append(str(f.relative_to(_PROJECT_ROOT)))
    assert not missing_import, "以下文件调用 get_observable_state 但未 import:\n" + "\n".join(missing_import)


def test_get_observable_state_importable_from_ui_i18n():
    """get_observable_state 可从 ui.i18n 导入并返回 I18nState 实例."""
    state = get_observable_state()
    assert isinstance(state, I18nState)
