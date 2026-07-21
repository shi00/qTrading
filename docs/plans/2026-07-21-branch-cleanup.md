# Safe Branch Cleanup Implementation Plan

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** Provide a safe, interactive Python script to clean up local and remote Git branches that have been merged to main, while strictly preserving protected branches (main/master), unmerged branches, and branches currently in use by Git worktrees.

**Architecture:** A standalone Python script executing `git` subprocesses to analyze branch status, filter them using strict white-lists and worktree mappings, show a double-check summary table, and execute batch deletion using `git branch -d` and `git push origin --delete` after receiving explicit `yes` confirmation.

**Tech Stack:** Python 3 (standard library: `subprocess`, `sys`, `re`), Git.

---

## User Review Required

> [!IMPORTANT]
> This plan performs irreversible deletion of local and remote branches. Safety checks (worktree preservation, `--merged` check, `yes` confirmation) are implemented to prevent any data loss.

## Proposed Changes

### Branch Cleanup Script

#### [NEW] [safe_cleanup_branches.py](file:///d:/workspace/Quantitative%20Trading/astock_screener/scripts/safe_cleanup_branches.py)
#### [NEW] [test_safe_cleanup_branches.py](file:///d:/workspace/Quantitative%20Trading/astock_screener/tests/test_safe_cleanup_branches.py)

---

### Task 1: Write Unit Tests for Branch Analysis & Filtering Logic

**Files:**
- Create: `tests/test_safe_cleanup_branches.py`

**Step 1: Write failing tests**
Write mock tests for branch filtering logic (e.g., verifying `main` is protected, worktree branches are protected, unmerged branches are protected).

**Step 2: Run tests to verify they fail**
Run: `pytest tests/test_safe_cleanup_branches.py`
Expected: fails because `safe_cleanup_branches.py` does not exist yet.

---

### Task 2: Implement core logic in `scripts/safe_cleanup_branches.py`

**Files:**
- Create: `scripts/safe_cleanup_branches.py`

**Step 1: Write minimal implementation**
Implement helper functions:
- `get_worktree_branches()`
- `get_merged_local_branches(base_branch="main")`
- `get_merged_remote_branches(base_branch="origin/main")`
- `filter_branches(local_branches, remote_branches, worktree_branches)`

**Step 2: Run tests to verify they pass**
Run: `pytest tests/test_safe_cleanup_branches.py`
Expected: PASS.

---

### Task 3: Implement CLI UI & Execution Logic in `scripts/safe_cleanup_branches.py`

**Files:**
- Modify: `scripts/safe_cleanup_branches.py`

**Step 1: Write CLI code**
Add user prompt (`yes` check) and execution code (`git branch -d`, `git push origin --delete`). Add `if __name__ == '__main__':` block.

**Step 2: Run manual verification**
Verify the output formatting and safe abort mechanism by running the script without typing `yes`.

---

## Verification Plan

### Automated Tests
- `pytest tests/test_safe_cleanup_branches.py`

### Manual Verification
- Run `python scripts/safe_cleanup_branches.py` in dry-run mode (press Enter or type anything other than `yes` to cancel) and verify that the printed output correctly identifies:
  - `main` as protected.
  - Worktree branches as protected.
  - Branches to delete.
