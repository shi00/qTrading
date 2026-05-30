import pytest

pytestmark = pytest.mark.e2e

from ui.i18n import I18n


async def test_diagnostic_nav_semantics(e2e_page):
    """诊断：检查 NavigationRail 在语义树中的表现。"""
    await e2e_page.page.wait_for_timeout(5000)

    nodes = await e2e_page.dump_semantics()
    nav_labels = [I18n.get(k) for k in ["nav_market", "nav_screener", "nav_settings"]]
    for label in nav_labels:
        matches = [n for n in nodes if label in (n.get("text", "") or "")]
        print(f"--- [DIAG] '{label}' in textContent: {len(matches)} matches → {matches[:3]}", flush=True)

        aria_matches = [n for n in nodes if label in (n.get("aria", "") or "")]
        print(f"--- [DIAG] '{label}' in aria-label: {len(aria_matches)} matches → {aria_matches[:3]}", flush=True)

    all_roles = set(n.get("role") for n in nodes if n.get("role"))
    print(f"--- [DIAG] All roles: {all_roles}", flush=True)

    for label in nav_labels:
        by_text = e2e_page.page.get_by_text(label, exact=False)
        count = await by_text.count()
        print(f"--- [DIAG] get_by_text('{label}'): count={count}", flush=True)

    print(f"--- [DIAG] Total semantics nodes: {len(nodes)}", flush=True)
