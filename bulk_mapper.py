from __future__ import annotations

import pandas as pd


TEMPLATE_VERSION = "Draft-v1-manual-review-required"


def suggestions_to_bulk_draft(suggestions: pd.DataFrame) -> pd.DataFrame:
    if suggestions.empty:
        return pd.DataFrame()
    executable = suggestions[suggestions["action_type"].isin(["Increase Bid", "Decrease Bid", "Add Negative Exact", "Add Negative Phrase", "Increase Budget", "Decrease Budget"])].copy()
    return pd.DataFrame({
        "Record Type": executable["action_type"],
        "Campaign": executable["campaign"],
        "Ad Group": executable["ad_group"],
        "Keyword or Product Targeting": executable["targeting"].where(executable["targeting"].ne(""), executable["search_term"]),
        "Match Type": executable["match_type"],
        "Bid": executable["recommended_bid"],
        "Budget": executable["recommended_budget"],
        "Operation": "Update Draft",
        "Requires Manual Review": "Yes",
        "Template Version": TEMPLATE_VERSION,
        "Reason": executable["reason"],
    })
