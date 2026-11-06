"""C02：八种成果封闭合同（§10.2.1）行为测试。"""

from uuid import UUID

import pytest

from core.agent_types import AgentValidationError, json_doc, validate_artifact

RESOURCE_ID = "10000000-0000-0000-0000-000000000001"
ARTIFACT_ID = "20000000-0000-0000-0000-000000000001"
DIGEST = "a" * 64


def _common(**extra):
    content = {
        "schema_version": 1,
        "as_of": "2026-04-30",
        "facts": [],
        "interpretations": [],
        "uncertainties": [],
        "limitations": [],
        "evidence": [],
    }
    content.update(extra)
    return content


def _evidence_ref() -> dict:
    return {"claim_id": "a1", "resource_id": RESOURCE_ID, "revision": 1, "locator": "财务表"}


def _evidence() -> dict:
    return {
        "claim_id": "a1",
        "resource_id": RESOURCE_ID,
        "revision": 1,
        "locator": "财务表",
        "published_at": None,
        "published_date": "2026-04-30",
        "time_precision": "date",
    }


def _formula(field: str, numerator: str, denominator: str, result: str | None, reason: str | None):
    return {
        "field": field,
        "numerator": numerator,
        "denominator": denominator,
        "result": result,
        "reason": reason,
        "evidence_refs": [_evidence_ref()],
    }


def _comparison_row(cash_ratio: str = "0.6") -> dict:
    return {
        "object": "甲",
        "period": "2025年度",
        "scope": "合并",
        "revenue": {"value": "100", "unit": "亿元", "reason": None},
        "net_profit": {"value": "10", "unit": "亿元", "reason": None},
        "operating_cash_flow": {"value": "6", "unit": "亿元", "reason": None},
        "net_margin": "0.1",
        "cash_profit_ratio": cash_ratio,
        "formulas": [
            _formula("net_margin", "net_profit", "revenue", "0.1", None),
            _formula("cash_profit_ratio", "operating_cash_flow", "net_profit", cash_ratio, None),
        ],
        "evidence_refs": [_evidence_ref()],
        "limitations": ["单项指标不能证明整体优劣"],
    }


def _comparison_content(row: dict | None = None) -> dict:
    return _common(
        facts=[{"claim_id": "a1", "text": "甲实现营业收入100亿元"}],
        evidence=[_evidence()],
        rows=[row or _comparison_row()],
    )


def _candidate(object_name: str, result: str) -> dict:
    check = {
        "condition_id": "c1",
        "actual": "10" if result != "unknown" else None,
        "unit": "亿元",
        "result": result,
        "reason": None if result != "unknown" else "数据缺失",
        "evidence_refs": [],
    }
    return {"object": object_name, "checks": [check], "evidence_refs": [], "limitations": []}


def _candidates_content() -> dict:
    return _common(
        pool_ref={"resource_id": RESOURCE_ID, "revision": 1},
        conditions=[{"condition_id": "c1", "field": "revenue", "operator": "gt", "threshold": "5", "unit": "亿元"}],
        coverage={"examined": 1, "total": 3, "verified": 1},
        matched=[_candidate("甲", "pass")],
        not_matched=[],
        unverified=[],
    )


def test_answer_accepts_minimal_content_and_rejects_unknown_field():
    content = _common(answer="现金净利比是经营现金流与净利润之比")
    validate_artifact("answer/1", json_doc(content))

    content["extra"] = True
    with pytest.raises(AgentValidationError) as error:
        validate_artifact("answer/1", json_doc(content))
    assert error.value.field_path == "content.extra"


def test_answer_requires_answer_text_and_rejects_missing_as_of_rule_violation():
    with pytest.raises(AgentValidationError) as error:
        validate_artifact("answer/1", json_doc(_common()))
    assert error.value.field_path == "content.answer"


def test_non_answer_contracts_require_as_of():
    content = _common(
        as_of=None,
        stock_result={
            "conclusion_label": None,
            "score": None,
            "confidence": None,
            "uncertainty_factors": None,
            "thinking": None,
            "summary": None,
        },
    )
    with pytest.raises(AgentValidationError) as error:
        validate_artifact("stock_analysis/1", json_doc(content))
    assert error.value.field_path == "content.as_of"


def test_stock_analysis_rejects_out_of_range_score_and_unknown_label():
    valid = _common(
        stock_result={
            "conclusion_label": "watchlist",
            "score": 72.5,
            "confidence": 0.8,
            "uncertainty_factors": None,
            "thinking": None,
            "summary": "观察",
        }
    )
    validate_artifact("stock_analysis/1", json_doc(valid))

    valid["stock_result"]["conclusion_label"] = "buy_now"
    with pytest.raises(AgentValidationError) as error:
        validate_artifact("stock_analysis/1", json_doc(valid))
    assert error.value.field_path == "content.stock_result.conclusion_label"


def test_comparison_accepts_cq01_row_and_rejects_wrong_ratio():
    validate_artifact("comparison/1", json_doc(_comparison_content()))

    wrong = _comparison_row("1.67")
    wrong["formulas"][1]["result"] = "0.6"
    with pytest.raises(AgentValidationError) as error:
        validate_artifact("comparison/1", json_doc(_comparison_content(wrong)))
    assert error.value.field_path == "content.rows[0].cash_profit_ratio"


def test_comparison_rejects_mixed_units_and_missing_formula():
    content = _comparison_content()
    content["rows"][0]["net_profit"]["unit"] = "万元"
    with pytest.raises(AgentValidationError) as error:
        validate_artifact("comparison/1", json_doc(content))
    assert error.value.code == "unsupported_unit"

    content = _comparison_content()
    content["rows"][0]["formulas"] = content["rows"][0]["formulas"][:1]
    with pytest.raises(AgentValidationError) as error:
        validate_artifact("comparison/1", json_doc(content))
    assert error.value.field_path == "content.rows[0].formulas"


def test_comparison_null_row_requires_reason_and_null_results():
    row = _comparison_row()
    row["revenue"] = {"value": None, "unit": "亿元", "reason": "未取得收入数据"}
    row["net_profit"] = {"value": None, "unit": "亿元", "reason": "未取得净利数据"}
    row["operating_cash_flow"] = {"value": None, "unit": "亿元", "reason": "未取得现金流数据"}
    row["net_margin"] = None
    row["cash_profit_ratio"] = None
    row["formulas"] = [
        _formula("net_margin", "net_profit", "revenue", None, "操作数缺失"),
        _formula("cash_profit_ratio", "operating_cash_flow", "net_profit", None, "操作数缺失"),
    ]
    validate_artifact("comparison/1", json_doc(_comparison_content(row)))


def test_candidates_coverage_must_match_the_three_groups():
    validate_artifact("candidates/1", json_doc(_candidates_content()))

    content = _candidates_content()
    content["coverage"]["examined"] = 2
    with pytest.raises(AgentValidationError) as error:
        validate_artifact("candidates/1", json_doc(content))
    assert error.value.field_path == "content.coverage.examined"


def test_candidates_unknown_check_goes_to_unverified():
    content = _candidates_content()
    content["matched"] = []
    content["unverified"] = [_candidate("甲", "unknown")]
    content["coverage"] = {"examined": 1, "total": 3, "verified": 0}
    validate_artifact("candidates/1", json_doc(content))

    content["matched"] = [_candidate("甲", "unknown")]
    content["unverified"] = []
    with pytest.raises(AgentValidationError) as error:
        validate_artifact("candidates/1", json_doc(content))
    assert error.value.code == "calculation_mismatch"


def test_news_occurrence_precision_is_joint():
    content = _common(
        events=[
            {
                "event_id": "e1",
                "occurred_at": {"at": None, "date": None, "precision": "unknown"},
                "published_at": {"at": "2026-04-30T00:00:00.000000Z", "date": None, "precision": "datetime"},
                "source_kind": "announcement",
                "fact_or_opinion": "fact",
                "affected_objects": ["甲"],
                "description": "计划检修",
                "evidence_refs": [],
                "limitations": [],
            }
        ],
        excluded_sources=[
            {"ref": {"resource_id": RESOURCE_ID, "revision": 1}, "reason": "after_as_of", "detail": "晚于截止日"}
        ],
    )
    validate_artifact("news_digest/1", json_doc(content))

    content["events"][0]["occurred_at"]["at"] = "2026-04-30T00:00:00.000000Z"
    with pytest.raises(AgentValidationError) as error:
        validate_artifact("news_digest/1", json_doc(content))
    assert error.value.field_path == "content.events[0].occurred_at.at"


def test_review_changes_only_with_confirmed_error():
    content = _common(
        challenged_claim="现金净利比为0.6",
        original_artifact_ref={"artifact_id": ARTIFACT_ID, "revision": 1},
        checks=[],
        verdict="no_error_found",
        changes=[],
    )
    validate_artifact("review/1", json_doc(content))

    content["changes"] = [
        {
            "claim_id": "a1",
            "field_path": "cash_profit_ratio",
            "before": "0.6",
            "after": "1.2",
            "unit": None,
            "explanation": "更正",
            "evidence_refs": [],
        }
    ]
    with pytest.raises(AgentValidationError) as error:
        validate_artifact("review/1", json_doc(content))
    assert error.value.field_path == "content.changes"


def test_dated_update_requires_ordered_as_of_and_matching_top_level():
    content = _common(
        as_of="2026-04-30",
        old_as_of="2026-04-01",
        new_as_of="2026-04-30",
        old_artifact_ref={"artifact_id": ARTIFACT_ID, "revision": 1},
        added_evidence=[],
        changed_claims=[],
        unchanged_claims=[],
    )
    validate_artifact("dated_update/1", json_doc(content))

    content["old_as_of"] = "2026-05-30"
    with pytest.raises(AgentValidationError) as error:
        validate_artifact("dated_update/1", json_doc(content))
    assert error.value.field_path == "content.old_as_of"


def test_backtest_unknown_warnings_are_not_missing_schema_keys():
    report_ref = {"resource_id": RESOURCE_ID, "revision": 1}
    content = _common(
        uncertainties=["质量字段未提供"],
        limitations=["仅已有报告解读"],
        backtest_refs=[report_ref],
        config_snapshots=[
            {
                "report_ref": report_ref,
                "raw_config_ref": None,
                "strategy_key": "S1",
                "strategy_version": "1",
                "from_date": "2025-01-02",
                "until_date": "2025-12-31",
                "parameters": [],
                "limitations": ["原配置未提供"],
            }
        ],
        comparability=[],
        trade_assumptions=[
            {
                "report_ref": report_ref,
                "signal_to_trade": None,
                "cost_value": None,
                "cost_unit": None,
                "universe_basis": "unknown",
                "industry_basis": "unknown",
                "limitations": ["成交和历史维度未知"],
            }
        ],
        attribution_limits=["质量未知不能证明无问题"],
    )
    with pytest.raises(AgentValidationError) as error:
        validate_artifact("backtest_explanation/1", json_doc(content))
    assert error.value.field_path == "content.data_warnings"

    content["data_warnings"] = None
    validate_artifact("backtest_explanation/1", json_doc(content))


def test_unknown_output_contract_is_unsupported():
    with pytest.raises(AgentValidationError) as error:
        validate_artifact("answer/2", json_doc(_common(answer="x")))
    assert error.value.code == "unsupported_schema"


def test_evidence_must_point_to_an_existing_claim():
    content = _common(answer="x", evidence=[_evidence()])
    content["evidence"][0]["claim_id"] = "missing"
    with pytest.raises(AgentValidationError) as error:
        validate_artifact("answer/1", json_doc(content))
    assert error.value.field_path == "content.evidence[0].claim_id"

    content = _common(answer="x", evidence=[_evidence()], facts=[{"claim_id": "a1", "text": "甲营业收入100亿元"}])
    validate_artifact("answer/1", json_doc(content))
