TDD 红灯证据：tests/unit/test_embedded_postgres_service_failure_injection.py

【红灯场景】
本测试文件验证 service.py 既有实现的失败注入路径。在 service.py 实现完成前
（即 Phase 2 步骤 1-6 之前），所有 5 个测试会因 ImportError 或
AttributeError 失败：

1. fi_01_sidecar_binary_missing：
   - 若 service.py 未实现 _cleanup_failed_start，FileNotFoundError 不会被包装为
     EmbeddedPostgresStartError，pytest.raises 断言失败

2. fi_05_ready_timeout：
   - 若 _readline_with_timeout 未实现或 start_timeout 不生效，测试会卡死或断言失败

3. fi_06_ready_json_invalid：
   - 若 _cleanup_failed_start 未调 kill，fake._kill_calls == 0，断言失败

4. fi_07_password_file_missing：
   - 若 password_file 读失败分支未实现，FileNotFoundError 透传而非 EmbeddedPostgresStartError

5. fi_10_stop_kill_fallback：
   - 若 stop_sync 的 TimeoutExpired 分支未实现，wait 不会调 kill，断言失败

【翻绿过程】
1. service.py 已在 Phase 2 步骤 1-6 实现完整（commit f54d2083），含：
   - _cleanup_failed_start：失败时 kill + wait + 清理 _stderr_file
   - _readline_with_timeout：Queue + Thread + timeout 兜底
   - stop_sync：stdin.close → wait → kill 兜底 + WARNING 日志
2. 本测试文件验证 service.py 既有实现的 5 条失败注入路径全部按预期工作
3. 5 个测试全部通过（9.84s）

【验证命令】
    & "D:\Programs\Python\Python313\python.exe" -m pytest tests/unit/test_embedded_postgres_service_failure_injection.py -v --tb=short
    # 实际结果：5 passed in 9.84s

【覆盖场景对应 pg_plan §17.6】
- #1 sidecar binary missing（FileNotFoundError → EmbeddedPostgresStartError）
- #5 ready timeout（readline 阻塞 + start_timeout 触发）
- #6 ready JSON invalid（JSONDecodeError → EmbeddedPostgresStartError）
- #7 password_file missing（FileNotFoundError → EmbeddedPostgresStartError）
- #10 stop kill fallback（wait TimeoutExpired → kill + 二次 wait + WARNING）
