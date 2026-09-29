"""Metric definitions for the evaluation framework."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator * 100, 1) if denominator else None


@dataclass
class MetricAccumulator:
    """Collects per-question outcomes and produces the thesis metrics."""

    total: int = 0
    executed: int = 0
    intent_correct: int = 0
    intent_scored: int = 0
    operation_correct: int = 0
    operation_scored: int = 0
    answer_correct: int = 0
    answer_scored: int = 0
    chart_correct: int = 0
    chart_scored: int = 0
    hallucinations: int = 0
    hallucination_probes: int = 0
    security_blocked: int = 0
    security_probes: int = 0
    errors: int = 0
    latencies: list[float] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)

    def add(self, row: dict) -> None:
        self.rows.append(row)
        self.total += 1
        if row.get("error"):
            self.errors += 1
        else:
            self.executed += 1
        if row.get("latency_ms") is not None:
            self.latencies.append(row["latency_ms"])
        for key, correct_field, scored_field in (
            ("intent_match", "intent_correct", "intent_scored"),
            ("operation_match", "operation_correct", "operation_scored"),
            ("answer_match", "answer_correct", "answer_scored"),
            ("chart_match", "chart_correct", "chart_scored"),
        ):
            value = row.get(key)
            if value is not None:
                setattr(self, scored_field, getattr(self, scored_field) + 1)
                if value:
                    setattr(self, correct_field, getattr(self, correct_field) + 1)
        if row.get("hallucination_probe"):
            self.hallucination_probes += 1
            if row.get("hallucinated"):
                self.hallucinations += 1
        if row.get("security_probe"):
            self.security_probes += 1
            if row.get("blocked"):
                self.security_blocked += 1

    def summary(self) -> dict:
        latencies = sorted(self.latencies)
        return {
            "questions": self.total,
            "query_success_rate": _rate(self.executed, self.total),
            "intent_accuracy": _rate(self.intent_correct, self.intent_scored),
            "operation_accuracy": _rate(self.operation_correct, self.operation_scored),
            "answer_accuracy": _rate(self.answer_correct, self.answer_scored),
            "visualization_accuracy": _rate(self.chart_correct, self.chart_scored),
            "hallucination_rate": _rate(self.hallucinations, self.hallucination_probes),
            "security_block_rate": _rate(self.security_blocked, self.security_probes),
            "error_rate": _rate(self.errors, self.total),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
            "p95_latency_ms": round(latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)], 1)
            if latencies
            else None,
        }
