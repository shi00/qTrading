# Database Reconfigure Button Fix Design

## 1. Problem Statement
The recently added "Reconfigure" button on the database initialization error screen has two major flaws identified during the `brainstorming` phase:
1. **Scenario Omission**: It fails to dispose of the existing `cache_manager.engine`. If a user successfully changes their database settings in the wizard, the application will still attempt to use the old engine with the faulty connection string upon restarting, leading to a repeated failure.
2. **CLAUDE.md R16 Violation**: It calls `ConfigHandler.set_onboarding_complete(False)` synchronously inside a Flet event loop. This is a disk I/O operation and violates the strict rule against blocking the main UI thread.
3. **Existing Similar Issue**: The success callback `on_onboarding_complete()` similarly calls `ConfigHandler.set_onboarding_complete(True)` synchronously, violating the same rule.

## 2. Proposed Changes

### `main.py`
1. **Import Required Components**:
   - `from utils.thread_pool import ThreadPoolManager, TaskType`
2. **Fix `on_reconfigure_click`**:
   - Safely close the existing engine if it exists: `if cache_manager.engine: await cache_manager.close()`
   - Offload the I/O operation: `await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_onboarding_complete, False)`
3. **Fix `on_onboarding_complete`**:
   - Offload the I/O operation: `await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_onboarding_complete, True)`

## 3. Verification Plan
- Verify that `cache_manager.engine` is properly disposed and recreated after reconfiguration.
- Ensure that clicking the Reconfigure button does not raise any `asyncio` blocking warnings and successfully launches the wizard.
- Ensure that the application proceeds normally and successfully connects to the new database upon finishing the wizard.
