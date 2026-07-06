from __future__ import annotations

from pathlib import Path

import pandas as pd

from .bulk_mapper import suggestions_to_bulk_draft


def export_bulk_draft(suggestions: pd.DataFrame, output_path: str | Path) -> Path:
    draft = suggestions_to_bulk_draft(suggestions)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    draft.to_excel(path, index=False)
    return path
