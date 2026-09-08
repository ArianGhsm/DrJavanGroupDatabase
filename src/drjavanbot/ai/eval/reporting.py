from __future__ import annotations

from .schema import QualityReport


def human_summary(report: QualityReport) -> str:
    a = report.aggregate
    lines = [
        f"Quality Lab {report.schema_version}: {'PASS' if report.passed else 'FAIL'}",
        f"archive={report.archive_files} files/{report.message_count} messages index={report.index_seconds:.3f}s db={report.db_size_bytes}B",
        f"cases={a.case_count} strict_failures={a.strict_failures} recall@{report.top_k}={_fmt(a.discussion_recall_at_k_mean)} mrr={a.mrr:.4f}",
        f"topic_relevance@{report.top_k}={_fmt(a.topic_relevance_at_k_mean)} facet_colocation={_fmt(a.facet_colocation_rate)} irrelevant={_fmt(a.irrelevant_candidate_rate_mean)}",
        f"retrieval_ms median={_fmt(a.median_retrieval_ms)} p95={_fmt(a.p95_retrieval_ms)} queries_median={_fmt(a.median_query_count)} hydration_median={_fmt(a.median_hydration_count)}",
        f"answerability false_insufficient={_fmt(a.false_insufficient_rate)} false_supported={_fmt(a.false_supported_rate)} grounding_verifier={report.grounding.verifier_accuracy:.4f}",
    ]
    if report.strict_failures:
        lines.append("strict: " + ", ".join(report.strict_failures))
    if report.grounding.failures:
        lines.append("grounding: " + ", ".join(report.grounding.failures))
    if report.scripted.failures:
        lines.append("scripted: " + ", ".join(report.scripted.failures))
    return "\n".join(lines)


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)
