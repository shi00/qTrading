"""
core — Architecture core layer (DDD).

This package contains cross-cutting infrastructure shared by all layers
(data, services, strategies, utils, ui) but must NOT depend on any of them
in order to avoid circular dependencies and reverse dependencies.

Dependency rule:  core ← data / services / strategies / utils / ui

    * core never imports from any other layer.
    * Any other layer may import from core (single-direction dependency).
    * In particular, `utils → core` is an ALLOWED single-direction
      dependency and does NOT violate core layer isolation
      (e.g. utils/technical_analysis.py, utils/scheduler_service.py and
      utils/error_classifier.py all `from core.i18n import I18n`).
      This is the intended pattern, not a circular dependency.

See CLAUDE.md §4.2 (core layer isolation principle) for the rationale.

Currently houses:
    - i18n: Internationalization (moved from ui/ to eliminate reverse dependency)
"""
