"""
core — Architecture core layer (DDD).

This package contains cross-cutting infrastructure shared by all layers
(data, services, strategies, utils, ui) but must NOT depend on any of them
in order to avoid circular dependencies and reverse dependencies.

Dependency rule:  core ← data / services / strategies / utils / ui

Currently houses:
    - i18n: Internationalization (moved from ui/ to eliminate reverse dependency)
"""
