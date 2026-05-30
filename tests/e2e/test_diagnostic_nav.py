import logging

import pytest

pytestmark = pytest.mark.e2e

from ui.i18n import I18n

logger = logging.getLogger(__name__)


async def test_diagnostic_nav_semantics(e2e_page):
    """诊断：检查 NavigationRail 在语义树中的表现。"""
    await e2e_page.page.wait_for_timeout(5000)

    nodes = await e2e_page.dump_semantics()
    nav_labels = [I18n.get(k) for k in ["nav_market", "nav_screener", "nav_settings"]]
    for label in nav_labels:
        matches = [n for n in nodes if label in (n.get("text", "") or "")]
        logger.info("'%s' in textContent: %d matches → %s", label, len(matches), matches[:3])

        aria_matches = [n for n in nodes if label in (n.get("aria", "") or "")]
        logger.info("'%s' in aria-label: %d matches → %s", label, len(aria_matches), aria_matches[:3])

    all_roles = set(n.get("role") for n in nodes if n.get("role"))
    logger.info("All roles: %s", all_roles)

    for label in nav_labels:
        by_text = e2e_page.page.get_by_text(label, exact=False)
        count = await by_text.count()
        logger.info("get_by_text('%s'): count=%d", label, count)

    logger.info("Total semantics nodes: %d", len(nodes))
