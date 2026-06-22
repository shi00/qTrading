# Database Reconfigure Button Fix Design

## 1. Problem Statement
The recently added "Reconfigure" button on the database initialization error screen has one major flaw identified during the `brainstorming` phase:
- **Scenario Omission**: It fails to dispose of the existing `cache_manager.engine`. If a user successfully changes their database settings in the wizard, the application will still attempt to use the old engine with the faulty connection string upon restarting, leading to a repeated failure.

## 2. Proposed Changes

### `main.py`
1. **Fix `on_reconfigure_click`**:
   - Safely close the existing engine by calling `await cache_manager.close()`.
   - This sets `self.engine = None` and `self._disposed = True` in the CacheManager singleton, enabling the initialization callback to recreate a fresh database engine with the new connection parameters.

## 3. Verification Plan
- Verify that `cache_manager.engine` is properly disposed and recreated after reconfiguration.
- Ensure that clicking the Reconfigure button successfully launches the wizard.
- Ensure that the application proceeds normally and successfully connects to the new database upon finishing the wizard.
