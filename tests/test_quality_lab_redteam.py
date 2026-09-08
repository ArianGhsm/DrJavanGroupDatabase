from drjavanbot.ai.eval.grounding import run_grounding_red_team
from drjavanbot.ai.eval.scripted import run_scripted_e2e


def test_grounding_red_team_detects_all_scripted_attacks():
    report = run_grounding_red_team()
    assert report.total >= 12
    assert report.passed == report.total
    assert report.verifier_accuracy == 1.0
    assert report.invalid_citation_rate == 0.0
    assert report.quote_mismatch_rate == 0.0
    assert report.unsupported_high_risk_claim_rate == 0.0
    assert not report.failures


def test_fake_provider_end_to_end_has_no_secret_or_network_dependency():
    report = run_scripted_e2e()
    assert report.total >= 5
    assert report.passed == report.total
    assert report.false_insufficient_rate == 0.0
    assert report.false_supported_rate == 0.0
    assert report.reason_code_accuracy == 1.0
    assert report.answerable_fragmented_recovery_rate == 1.0
    assert max(report.logical_calls_by_case.values()) <= 4
    assert all(tokens > 0 for tokens in report.token_budget_by_case.values())
