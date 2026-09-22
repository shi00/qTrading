# AI 代码检视附录

> **加载方式**：由 [ai-review.md](./ai-review.md) 按需引用。包含执行提示词与参考实践，不强制加载。

---

## A. 最小执行提示词

按规则 ID 引用核心协议，避免逐句复述（规则 ID 清单以 [ai-review.md](./ai-review.md) 为唯一正本）。

```text
你是一名资深代码审查员。请对指定软件变更或制品进行证据化检视。
按 [ai-review.md §2~§10](./ai-review.md) 的规则 ID 执行：
- 安全边界：SAFE-01…SAFE-05；
- 输入契约与检视模式：INPUT-01…03；MODE-01…05；
- 三轮检视：ROUND1-01…03；ROUND2-01…03；ROUND3-01…04（ROUND2-02 默认必检 6 类见 [quality-dimensions.md](./quality-dimensions.md)）；
- 停止条件：STOP-01…03；
- 发现分类/证据/严重度：FIND-01…02；EVID-01…02；SEV-01…02；
- 输出要求：OUT-01…04（机器可读须符合 review-result.schema.json）；
- 完成检查表：CHECK-01…15。

输出：
- 先给结论、范围、意图和最高风险；
- 发现按严重度排序，每项含类别、位置、变化关系、维度、置信度、触发条件、
  当前行为、预期行为、影响、证据、最小建议和验证方式；
- 测试缺口、待确认、待验证和建议分别列出；
- 给出不可变目标版本、范围清单、场景覆盖、已运行与未运行检查、证据来源、残余风险和策略版本；
- 不得声称未执行检查已通过；无发现不等于绝对无缺陷。
```

---

## B. 参考实践

本指南参考下列行业模型和实践，但不替代组织采用的正式版本：

- ISO/IEC 25010 软件产品质量模型；
- OWASP ASVS、OWASP Top 10、OWASP API Security Top 10；
- NIST Secure Software Development Framework；
- SEI CERT 编码规范；
- Google SRE 关于可靠性、容量、监控和故障处理的实践；
- WCAG 可访问性原则；
- Semantic Versioning、契约测试和消费者驱动契约；
- Twelve-Factor App 关于配置、依赖、进程和可观测性的原则；
- 状态迁移、判定表、边界值、属性测试和故障注入等测试技术。

使用时应以组织采用的具体版本、法规和项目规则为准。

---

## C. 最小合法 review-result 示例

> 对应 [ai-review.md](./ai-review.md) OUT-01 的「人类报告字段 ↔ schema 字段」对照；以下 JSON 为可通过 `review-result.schema.json` 校验的最小单发现结果（字段名/structure 与 schema `$defs` 对齐，`evidence` 等子结构从简）。

```json
{
  "schemaVersion": "1.0",
  "subject": {
    "repositoryId": "qtrading",
    "targetRevisionType": "commit",
    "targetRevision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "mergeBaseRevision": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "diffDigest": "0123456789abcdef0123456789abcdef",
    "reviewedFiles": ["ui/viewmodels/screener_view_model.py"]
  },
  "provenance": {
    "reviewedAt": "2026-09-22T10:00:00+08:00",
    "engine": "trae-ai",
    "engineVersion": "1.0.0",
    "promptVersion": "ai-review-v1.8.0",
    "toolVersions": {}
  },
  "review": {
    "mode": "incremental",
    "target": "fix/r21-score-none",
    "baseline": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "baselineStatus": "verified",
    "intent": "修复 score 缺失被回填 0 的 R21 违规",
    "verdict": "fail",
    "verdictReason": "发现 1 项未豁免阻断缺陷",
    "scopeLimitations": []
  },
  "findings": [
    {
      "id": "F-1",
      "fingerprint": "screener_vm:run:score-fill-0",
      "title": "score 缺失被回填为 0（R21 缺失值伪装）",
      "category": "defect",
      "severity": "P1",
      "confidence": "high",
      "changeRelation": "introduced",
      "dimension": "correctness",
      "ruleId": "R21",
      "disposition": "open",
      "waiver": null,
      "location": {
        "path": "ui/viewmodels/screener_view_model.py",
        "startLine": 42,
        "endLine": 42
      },
      "locationReason": "状态构造处",
      "trigger": "LLM 输出缺失 score 时",
      "actualBehavior": "填充 0.0 进入 state",
      "expectedBehavior": "使用 None 表示缺失",
      "impact": "UI 将缺失渲染为 0 分，误导选股结论",
      "evidence": [
        {
          "type": "runtime",
          "source": "pytest -k r21",
          "revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "location": null,
          "checkId": null,
          "artifactDigest": null,
          "summary": "test_score_none 断言失败：期望 None 实得 0.0",
          "trust": "repository",
          "redacted": false
        }
      ],
      "recommendation": "改为 score: float | None = None",
      "verification": "pytest tests/unit/ui/viewmodels/ -k r21"
    }
  ],
  "checks": {
    "executed": [
      {
        "id": "pytest-r21",
        "redactedCommand": "pytest tests/unit/ui/viewmodels/ -k r21 -q",
        "redactions": [],
        "environment": "linux-x86_64",
        "exitCode": 0,
        "status": "passed",
        "selectedCount": 1,
        "executedCount": 1,
        "skippedCount": 0,
        "durationMs": 120,
        "evidenceSummary": "1 passed",
        "reportDigest": null,
        "sideEffects": []
      }
    ],
    "notExecuted": []
  },
  "coverage": {
    "dimensions": ["correctness", "r21-missing-representation"],
    "scenarios": ["LLM 缺失 score", "正常 score"],
    "uncovered": [],
    "totalFiles": 1,
    "fileInventory": [
      {
        "path": "ui/viewmodels/screener_view_model.py",
        "status": "read",
        "highRisk": true,
        "reason": null,
        "authority": null
      }
    ]
  },
  "residualRisks": [],
  "gate": {
    "policyId": "ai-review-core",
    "policyVersion": "1.0.0",
    "evaluatorVersion": "1.0.0",
    "verdict": "fail",
    "decisions": [
      {
        "fingerprint": "screener_vm:run:score-fill-0",
        "blocking": true,
        "reason": "R21 缺失值伪装：缺失必须用 None 表示"
      }
    ]
  }
}
```

> 说明：`evidence` 为数组（`minItems: 1`）；`disposition: waived` 时 `waiver` 必须为对象；`gate.decisions[].fingerprint` 必须现存且唯一（OUT-02 语义校验器强制）。
