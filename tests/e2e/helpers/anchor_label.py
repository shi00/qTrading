"""Anchor 独立 label 通道的 DOM 快照契约与 fail-closed 断言。

``LABEL_INDEPENDENCE_JS`` 在浏览器中按 identifier 定位语义节点并返回三态快照；
``assert_label_independent`` 是不依赖 Playwright 的纯逻辑，负责对快照逐项断言。
拆分到独立模块是为了让判定逻辑可在 unit 层直接测试（无需 playwright/浏览器）。

契约（D21-OTHER-007）：
- ``count``: 带该 identifier 的语义节点数（0=目标缺失、>1=歧义）
- ``own_label_matches``: 自身 ``aria-label`` 恰等于 EID（独立名称通道）
- ``parent_found``: 存在父语义节点
- ``parent_has_eid``: 父节点 ``aria-label`` 含子 EID（container=True 失效）

修复前的旧 JS 在目标或父节点不存在时返回 ``false``，外层 ``assert not
parent_has_eid`` 反而通过（fail-open）；本模块要求每个前提显式成立。
"""

from collections.abc import Mapping

LABEL_INDEPENDENCE_JS = r"""(eid) => {
    const nodes = Array.from(document.querySelectorAll('flt-semantics[flt-semantics-identifier]'))
        .filter((el) => el.getAttribute('flt-semantics-identifier') === eid);
    if (nodes.length !== 1) {
        return {
            count: nodes.length,
            found: nodes.length > 0,
            own_label_matches: false,
            parent_found: false,
            parent_has_eid: false,
        };
    }
    const el = nodes[0];
    const parent = el.parentElement;
    const parentLabel = parent === null ? null : (parent.getAttribute('aria-label') || '');
    return {
        count: 1,
        found: true,
        own_label_matches: (el.getAttribute('aria-label') || '') === eid,
        parent_found: parent !== null,
        parent_has_eid: parentLabel !== null && parentLabel.includes(eid),
    };
}"""


def assert_label_independent(snapshot: object, eid: str) -> None:
    """断言 anchor 的独立 label 通道成立（fail-closed）。

    每个前提都显式断言；不得把“目标或父节点不存在”当作满足条件。
    """
    if not isinstance(snapshot, Mapping):
        raise AssertionError(f"label 独立性快照类型非法（期望 Mapping）: {type(snapshot).__name__}")
    if snapshot.get("found") is not True:
        raise AssertionError(f"anchor identifier 目标不存在: eid={eid!r}（独立 label 无法验证）")
    count = snapshot.get("count")
    if count != 1:
        raise AssertionError(f"anchor identifier 节点应唯一: eid={eid!r}, count={count!r}")
    if snapshot.get("own_label_matches") is not True:
        raise AssertionError(f"独立 label 通道缺失: eid={eid!r} 自身 aria-label 不等于 EID")
    if snapshot.get("parent_found") is not True:
        raise AssertionError(f"无法验证 label 独立性: eid={eid!r} 无父语义节点")
    if snapshot.get("parent_has_eid") is not False:
        raise AssertionError(f"container=True 未生效：父节点 aria-label 含子 EID {eid!r}，anchor 独立性被破坏")
