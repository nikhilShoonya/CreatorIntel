"""Generic CSV / Excel writer shared by both modules (cells sanitised against formula injection)."""

import io

import pandas as pd
from openpyxl.utils import get_column_letter

from app.utils.text import sanitize_spreadsheet_cell


def keep_whole_numbers(frame: pd.DataFrame) -> pd.DataFrame:
    """Whole-number columns with empty cells would otherwise be written as floats (20552.0)."""
    for column in frame.columns:
        values = frame[column].dropna()
        if len(values) and all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            frame[column] = frame[column].astype("Int64")
    return frame


def table_bytes(columns: list[str], rows: list[list], kind: str, sheet: str = "Data") -> bytes:
    """Generic CSV/Excel writer (cells sanitised against formula injection)."""
    frame = keep_whole_numbers(pd.DataFrame([[sanitize_spreadsheet_cell(v) for v in row] for row in rows], columns=columns))
    if kind == "csv":
        return frame.to_csv(index=False).encode("utf-8-sig")
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name=sheet)
        worksheet = writer.sheets[sheet]
        for index, column in enumerate(frame.columns, start=1):
            longest = max([len(str(column)), *(len(str(v)) for v in frame[column].head(200) if v is not None)])
            worksheet.column_dimensions[get_column_letter(index)].width = min(max(10, longest + 2), 70)
    return buffer.getvalue()
