from __future__ import annotations

from pathlib import Path

import pandas as pd

from .utils import ROOT, load_yaml


class ReportDetector:
    def __init__(self, config_path: str | Path | None = None):
        self.report_types = load_yaml(config_path or ROOT / "config" / "report_types.yaml").get("report_types", {})

    def detect(self, frame: pd.DataFrame) -> dict[str, str | int]:
        cols = set(frame.columns)
        best_key = "unknown"
        best_score = -1
        for key, spec in self.report_types.items():
            score = 0
            for field in spec.get("required_any", []):
                score += 3 if field in cols else 0
            for field in spec.get("weighted_fields", []):
                score += 2 if field in cols else 0
            if score > best_score:
                best_key = key
                best_score = score
        spec = self.report_types.get(best_key, {})
        if best_score <= 0:
            return {"report_key": "unknown", "report_name": "Unknown Report", "ad_type": "Unknown", "score": 0}
        return {
            "report_key": best_key,
            "report_name": spec.get("name", best_key),
            "ad_type": spec.get("ad_type", "Unknown"),
            "score": best_score,
        }

    def date_range(self, frame: pd.DataFrame) -> tuple[str, str]:
        if "date" not in frame.columns:
            return "", ""
        dates = pd.to_datetime(frame["date"], errors="coerce").dropna()
        if dates.empty:
            return "", ""
        return dates.min().date().isoformat(), dates.max().date().isoformat()
