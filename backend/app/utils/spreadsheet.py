"""Generic CSV / Excel writer shared by both modules (cells sanitised against formula injection)."""

import io

import pandas as pd
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.utils.text import sanitize_spreadsheet_cell


def keep_whole_numbers(frame: pd.DataFrame) -> pd.DataFrame:
    """Whole-number columns with empty cells would otherwise be written as floats (20552.0)."""
    for column in frame.columns:
        values = frame[column].dropna()
        if len(values) and all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            frame[column] = frame[column].astype("Int64")
    return frame


def table_bytes(columns: list[str], rows: list[list], kind: str, sheet: str = "Data", styled: bool = False) -> bytes:
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
        if styled:
            is_video_list = columns == ["Video Link", "Creator Name", "Platform", "Username"]
            is_performance_report = sheet == "Tracked videos"
            header_fill = PatternFill("solid", fgColor="394692")
            alternate_fill = PatternFill("solid", fgColor="F9FBFF")
            line = Side(style="thin", color="DCE3EE")
            border = Border(left=line, right=line, top=line, bottom=line)
            worksheet.sheet_view.showGridLines = False
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            worksheet.row_dimensions[1].height = 38
            for cell in worksheet[1]:
                cell.fill = header_fill
                cell.font = Font(name="Aptos", size=11, bold=True, color="FFFFFF")
                cell.alignment = Alignment(vertical="center", indent=1)
                cell.border = border
            for row in worksheet.iter_rows(min_row=2):
                worksheet.row_dimensions[row[0].row].height = 52 if is_video_list or is_performance_report else 42
                for cell in row:
                    if cell.row % 2:
                        cell.fill = alternate_fill
                    cell.font = Font(name="Aptos", size=11, color="1D2B48")
                    cell.alignment = Alignment(vertical="center", wrap_text=True, indent=1)
                    cell.border = border
                if is_video_list:
                    link, _, _, username = row
                    if isinstance(link.value, str) and link.value.startswith(("https://", "http://")):
                        link.hyperlink = link.value
                        link.font = Font(name="Aptos", size=11, color="3154B5", underline="single")
                    if username.value:
                        username.font = Font(name="Aptos", size=11, bold=True, color="007A4D")
                if is_performance_report:
                    link = row[1]
                    if isinstance(link.value, str) and link.value.startswith(("https://", "http://")):
                        link.hyperlink = link.value
                        link.font = Font(name="Aptos", size=11, color="3154B5", underline="single")
            for index, column in enumerate(columns, start=1):
                preferred = (
                    {"Video": 44, "Video URL": 58, "Platform": 16, "Creator": 28,
                     "Current Views": 18, "Previous Views": 18, "Views Gained": 18, "Growth %": 16,
                     "Likes": 14, "Comments": 14, "Engagement Rate (%)": 24, "Sentiment": 18,
                     "Sentiment Confidence": 25, "Tracking Status": 20, "Notes": 52,
                     "Last Checked (UTC)": 22, "Published (UTC)": 22, "Source": 16}.get(column)
                    if is_performance_report else
                    {"Video Link": 58, "Creator Name": 28, "Platform": 16, "Username": 26}.get(column)
                )
                if preferred:
                    worksheet.column_dimensions[get_column_letter(index)].width = preferred
    return buffer.getvalue()
