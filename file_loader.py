from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import chardet
import pandas as pd

from .utils import logger


@dataclass
class LoadedTable:
    frame: pd.DataFrame
    source_file: str
    sheet_name: str
    encoding: str | None
    errors: list[str]


def detect_encoding(data: bytes) -> str:
    result = chardet.detect(data[:200000])
    return result.get("encoding") or "utf-8-sig"


def read_uploaded_file(file: str | Path | BinaryIO, filename: str | None = None) -> list[LoadedTable]:
    name = filename or getattr(file, "name", str(file))
    suffix = Path(name).suffix.lower()
    errors: list[str] = []
    tables: list[LoadedTable] = []
    try:
        if suffix in [".csv", ".txt", ".tsv"]:
            if hasattr(file, "read"):
                data = file.read()
                if isinstance(data, str):
                    data = data.encode("utf-8")
            else:
                data = Path(file).read_bytes()
            encoding = detect_encoding(data)
            sep = "\t" if suffix == ".tsv" else None
            try:
                frame = pd.read_csv(pd.io.common.BytesIO(data), encoding=encoding, sep=sep, engine="python")
            except Exception:
                frame = pd.read_csv(pd.io.common.BytesIO(data), encoding="utf-8-sig", sep=sep, engine="python")
                encoding = "utf-8-sig"
            tables.append(LoadedTable(frame, Path(name).name, "CSV", encoding, errors))
        elif suffix in [".xlsx", ".xls"]:
            excel = pd.ExcelFile(file)
            for sheet in excel.sheet_names:
                try:
                    frame = pd.read_excel(excel, sheet_name=sheet)
                    if not frame.dropna(how="all").empty:
                        tables.append(LoadedTable(frame, Path(name).name, sheet, None, []))
                except Exception as exc:
                    msg = f"{Path(name).name}/{sheet} 读取失败: {exc}"
                    logger.exception(msg)
                    errors.append(msg)
        else:
            errors.append(f"不支持的文件类型: {suffix}")
    except Exception as exc:
        msg = f"{name} 读取失败: {exc}"
        logger.exception(msg)
        errors.append(msg)
    if not tables and errors:
        tables.append(LoadedTable(pd.DataFrame(), Path(name).name, "", None, errors))
    return tables


def load_many(files: list[str | Path | BinaryIO]) -> list[LoadedTable]:
    output: list[LoadedTable] = []
    for file in files:
        output.extend(read_uploaded_file(file))
    return output
