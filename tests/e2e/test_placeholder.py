"""
E2E Tests - Disabled (Flet 0.28.3 Desktop Mode)

Playwright e2e tests are disabled for Flet desktop applications.
Flet desktop apps use Flutter rendering engine directly,
not HTTP-exposed UI, making Playwright browser automation infeasible.

To re-enable when a suitable solution is found:
1. Implement proper e2e test framework for Flet desktop
2. Remove the skip marker below
3. Restore the original test cases from git history

Alternative approaches to consider:
- Flet's built-in testing capabilities (if available in future versions)
- Native Flutter testing framework integration
- Custom test harness using Flet's internal APIs
"""

import pytest

pytestmark = pytest.mark.skip(reason="Playwright not compatible with Flet 0.28.3 desktop mode")


class TestE2EPlaceholder:
    """Placeholder for future e2e tests"""

    def test_e2e_disabled_placeholder(self):
        """Placeholder test - e2e tests are currently disabled"""
        pass
