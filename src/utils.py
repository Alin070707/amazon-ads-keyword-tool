from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)


def setup_logger(name: str = "amazon_ads_optimizer") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    file_handler = logging.FileHandler(LOG_DIR / "app.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


logger = setup_logger()


def load_yaml(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def safe_divide(numerator: Any, denominator: Any, default: float = 0.0) -> Any:
    try:
        return numerator / denominator.replace(0, float("nan")).fillna(float("nan"))
    except AttributeError:
        return default if denominator in (0, None) else numerator / denominator


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def contains_any(text: str, terms: list[str]) -> bool:
    low = normalize_text(text)
    return any(normalize_text(term) in low for term in terms if normalize_text(term))
