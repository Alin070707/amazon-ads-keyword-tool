from __future__ import annotations

import difflib
import re
from pathlib import Path

import pandas as pd

from .utils import ROOT, load_yaml, normalize_text


def _clean_header(header: str) -> str:
    return re.sub(r"\s+", " ", str(header).strip()).lower()


class FieldMapper:
    def __init__(self, config_path: str | Path | None = None):
        config = load_yaml(config_path or ROOT / "config" / "field_aliases.yaml")
        self.aliases: dict[str, list[str]] = config.get("canonical_fields", {})
        self.lookup: dict[str, str] = {}
        for canonical, names in self.aliases.items():
            self.lookup[_clean_header(canonical)] = canonical
            for name in names:
                self.lookup[_clean_header(name)] = canonical

    def suggest_mapping(self, columns: list[str]) -> dict[str, str | None]:
        mapping: dict[str, str | None] = {}
        keys = list(self.lookup.keys())
        for col in columns:
            clean = _clean_header(col)
            if clean in self.lookup:
                mapping[col] = self.lookup[clean]
                continue
            match = difflib.get_close_matches(clean, keys, n=1, cutoff=0.82)
            mapping[col] = self.lookup[match[0]] if match else None
        return mapping

    def apply_mapping(self, frame: pd.DataFrame, manual_mapping: dict[str, str] | None = None) -> tuple[pd.DataFrame, dict[str, str | None], list[str]]:
        auto = self.suggest_mapping([str(c) for c in frame.columns])
        final = dict(auto)
        if manual_mapping:
            for old, new in manual_mapping.items():
                if new:
                    final[old] = new
        rename = {old: new for old, new in final.items() if new}
        mapped = frame.rename(columns=rename).copy()
        duplicated = [c for c in mapped.columns if list(mapped.columns).count(c) > 1]
        for col in sorted(set(duplicated)):
            same = mapped.loc[:, mapped.columns == col]
            merged = same.bfill(axis=1).iloc[:, 0]
            mapped = mapped.loc[:, mapped.columns != col]
            mapped[col] = merged
        unmapped = [str(c) for c, v in final.items() if v is None]
        return mapped, final, unmapped

    def canonical_choices(self) -> list[str]:
        return sorted(self.aliases.keys())


def classify_term(text: str, brand_terms: list[str], competitor_terms: list[str], core_terms: list[str]) -> str:
    low = normalize_text(text)
    if any(normalize_text(t) and normalize_text(t) in low for t in brand_terms):
        return "品牌词"
    if any(normalize_text(t) and normalize_text(t) in low for t in competitor_terms):
        return "竞品词"
    if any(normalize_text(t) and normalize_text(t) in low for t in core_terms):
        return "核心词"
    return "泛词"
