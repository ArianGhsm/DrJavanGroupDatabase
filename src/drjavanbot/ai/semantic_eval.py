"""Stable public entrypoint for the v2 Quality Lab.

This module intentionally contains evaluation-only imports. Production search and
answering code does not depend on the Quality Lab.
"""
from .eval import REPORT_SCHEMA_VERSION, QualityReport, golden_cases, run_quality_eval

__all__ = ["REPORT_SCHEMA_VERSION", "QualityReport", "golden_cases", "run_quality_eval"]
