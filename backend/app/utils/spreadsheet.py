"""CSV / Excel writer shared by every download in both modules.

Cells are sanitised against formula injection. Excel files get one consistent, readable design:
a coloured header with filters, frozen header row (and name column), number formats with
thousands separators, signed green/red growth, coloured status/sentiment cells, clickable links,
banded rows, sensible column widths, print settings and an "About" sheet with export details.
The header is always row 1 of the first sheet, so files stay easy to re-open, sort and re-import.
CSV is plain text by definition (no formatting); it is written as UTF-8 with BOM so Excel shows
Hindi/Telugu/emoji text correctly.
"""

import io
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.utils import get_column_letter

from app.utils.text import sanitize_spreadsheet_cell

# ---------------------------------------------------------------- design tokens (match the web app)
FONT = "Calibri"  # available in every Excel / LibreOffice / Google Sheets install
INK, MUTED, LINK = "1F2937", "94A3B8", "4338CA"
HEADER_FILL, HEADER_TEXT = "4338CA", "FFFFFF"
BAND_FILL, RULE = "F7F8FC", "E5E7EB"

_TONES = {  # (text, background)
    "green": ("047857", "ECFDF5"),
    "red": ("BE123C", "FFF1F2"),
    "amber": ("B45309", "FFFBEB"),
    "gray": ("475569", "F1F5F9"),
    "blue": ("4338CA", "EEF2FF"),
}
_VALUE_TONES = {
    # tracking / creator status
    "tracking": "green", "completed": "green", "active": "green",
    "video down": "red", "failed": "red",
    "partial": "amber", "unsupported": "amber",
    "paused": "gray", "pending": "gray", "processing": "blue", "queued": "gray",
    # upload results
    "added": "green", "already tracked": "gray", "duplicate": "amber", "invalid": "red",
    "skipped": "gray", "cached": "gray",
    # sentiment
    "positive": "green", "neutral": "gray", "negative": "red",
}
_PLATFORM_COLORS = {"youtube": "CC0000", "instagram": "C13584"}
_PLACEHOLDERS = {"N/A", "-", "Never", ""}

# Number formats. Growth values are already percentages (12.5 = 12.5 %).
FMT_INT = "#,##0"
FMT_SIGNED_INT = '[Color10]+#,##0;[Red]-#,##0;0'
FMT_PCT = '0.00"%"'
FMT_SIGNED_PCT = '[Color10]+0.00"%";[Red]-0.00"%";0.00"%"'
FMT_FRACTION_PCT = "0%"
FMT_DATE = "dd mmm yyyy, hh:mm"


def keep_whole_numbers(frame: pd.DataFrame) -> pd.DataFrame:
    """Whole-number columns with empty cells would otherwise be written as floats (20552.0)."""
    for column in frame.columns:
        values = frame[column].dropna()
        if len(values) and all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            frame[column] = frame[column].astype("Int64")
    return frame


def local_time(value: datetime | None, tz_name: str) -> datetime | None:
    """A timezone-aware datetime as naive local time (Excel cells cannot store a timezone)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    return value.astimezone(tz).replace(tzinfo=None)


def tz_label(tz_name: str) -> str:
    """Short label for column headers, e.g. 'IST'."""
    try:
        return datetime.now(ZoneInfo(tz_name)).tzname() or tz_name
    except Exception:
        return "UTC"


# ---------------------------------------------------------------- column roles
def _kind(header: str, values: list) -> str:
    name = header.lower()
    sample = [v for v in values[:200] if v is not None and v not in _PLACEHOLDERS]
    if sample and all(isinstance(v, datetime) for v in sample):
        return "date"
    if name == "row" or name == "#":
        return "index"
    if "views gained" in name:
        return "signed_int"
    if name.startswith("growth"):
        return "signed_pct"
    if "engagement rate" in name:
        return "pct"
    if "confidence" in name:
        return "fraction_pct"
    if ("views" in name and "url" not in name) or name in {"likes", "comments", "subscribers / followers", "followers", "subscribers"}:
        return "int"
    if "url" in name or "link" in name:
        return "url"
    if name in {"status", "tracking status", "result", "sentiment"}:
        return "badge"
    if name == "platform":
        return "platform"
    if name in {"notes", "video", "top performing video", "title", "caption", "message", "why it was not tracked"}:
        return "long_text"
    return "text"


_NUMERIC = {"index", "int", "signed_int", "pct", "signed_pct", "fraction_pct"}
_FORMATS = {"int": FMT_INT, "signed_int": FMT_SIGNED_INT, "pct": FMT_PCT, "signed_pct": FMT_SIGNED_PCT,
            "fraction_pct": FMT_FRACTION_PCT, "date": FMT_DATE, "index": "0"}
# (min, max) column widths in characters
_WIDTHS = {"index": (7, 8), "int": (12, 18), "signed_int": (12, 16), "pct": (12, 16), "signed_pct": (11, 14),
           "fraction_pct": (12, 14), "date": (19, 20), "url": (24, 46), "badge": (12, 18), "platform": (11, 13),
           "long_text": (24, 52), "text": (12, 32)}


def _display_len(value: object, kind: str) -> int:
    if value is None:
        return 0
    if kind in _NUMERIC and isinstance(value, (int, float)):
        return len(f"{value:,.2f}") + 1
    if kind == "date":
        return 18
    return max((len(part) for part in str(value).splitlines()), default=0)


# ---------------------------------------------------------------- writer
def table_bytes(
    columns: list[str],
    rows: list[list],
    kind: str,
    sheet: str = "Data",
    info: dict[str, object] | None = None,
) -> bytes:
    """Write rows as CSV or a formatted Excel workbook."""
    if kind == "csv":
        # CSV has no cell types, so text that starts like a formula is neutralised with a leading quote.
        clean = [[sanitize_spreadsheet_cell(v.strftime("%Y-%m-%d %H:%M") if isinstance(v, datetime) else v) for v in row] for row in rows]
        return keep_whole_numbers(pd.DataFrame(clean, columns=columns)).to_csv(index=False).encode("utf-8-sig")
    # Excel: text is written as text cells (never formulas), so "@username" stays exactly as it is.
    clean = [[ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v for v in row] for row in rows]
    return _excel(columns, clean, sheet, info or {})


def _as_text(cell) -> None:
    """openpyxl treats strings starting with '=' as formulas; force plain text."""
    if cell.data_type == "f":
        cell.data_type = "s"


def _excel(columns: list[str], rows: list[list], sheet_name: str, info: dict[str, object]) -> bytes:
    book = Workbook()
    ws = book.active
    ws.title = sheet_name[:31]
    kinds = [_kind(c, [r[i] for r in rows]) for i, c in enumerate(columns)]

    # Shared style objects (openpyxl stores each distinct style once).
    thin = Side(style="thin", color=RULE)
    header_font = Font(name=FONT, size=11, bold=True, color=HEADER_TEXT)
    header_fill = PatternFill("solid", fgColor=HEADER_FILL)
    header_border = Border(bottom=Side(style="medium", color="312E81"))
    body_font = Font(name=FONT, size=11, color=INK)
    strong_font = Font(name=FONT, size=11, color=INK, bold=True)
    muted_font = Font(name=FONT, size=11, color=MUTED, italic=True)
    link_font = Font(name=FONT, size=11, color=LINK, underline="single")
    band_fill = PatternFill("solid", fgColor=BAND_FILL)
    row_border = Border(bottom=thin)
    tone_styles = {
        tone: (Font(name=FONT, size=11, bold=True, color=text), PatternFill("solid", fgColor=bg))
        for tone, (text, bg) in _TONES.items()
    }
    platform_fonts = {p: Font(name=FONT, size=11, bold=True, color=c) for p, c in _PLATFORM_COLORS.items()}
    align = {
        "left": Alignment(horizontal="left", vertical="center", indent=1),
        "wrap": Alignment(horizontal="left", vertical="center", wrap_text=True, indent=1),
        "right": Alignment(horizontal="right", vertical="center", indent=1),
        "center": Alignment(horizontal="center", vertical="center"),
        "header": Alignment(horizontal="left", vertical="center", wrap_text=True, indent=1),
        "header_right": Alignment(horizontal="right", vertical="center", wrap_text=True, indent=1),
    }

    # Header
    ws.append(columns)
    for index, cell in enumerate(ws[1]):
        _as_text(cell)
        cell.font, cell.fill, cell.border = header_font, header_fill, header_border
        cell.alignment = align["header_right"] if kinds[index] in _NUMERIC else align["header"]
    ws.row_dimensions[1].height = 34

    # Column widths: fit the content within sensible limits (+3 for the filter button in the header).
    widths = []
    for index, column in enumerate(columns):
        low, high = _WIDTHS[kinds[index]]
        lengths = sorted(_display_len(r[index], kinds[index]) for r in rows[:500]) or [0]
        typical = lengths[min(len(lengths) - 1, int(len(lengths) * 0.9))]  # ignore a few very long outliers
        header_need = min(len(column), 22) + 3
        width = max(low, header_need, min(high, typical + 2))
        widths.append(width)
        ws.column_dimensions[get_column_letter(index + 1)].width = width

    banded = len(rows) <= 20000
    for row_index, values in enumerate(rows, start=2):
        ws.append(values)
        cells = ws[row_index]
        band = banded and row_index % 2 == 1
        lines = 1
        for index, cell in enumerate(cells):
            kind, value = kinds[index], cell.value
            _as_text(cell)
            cell.border = row_border
            if band:
                cell.fill = band_fill
            if value is None or (isinstance(value, str) and value in _PLACEHOLDERS):
                cell.font = muted_font
                cell.alignment = align["right"] if kind in _NUMERIC else align["left"]
                continue
            if kind in _NUMERIC or kind == "date":
                cell.font = body_font
                if isinstance(value, (int, float, datetime)):
                    cell.number_format = _FORMATS[kind]
                cell.alignment = align["right"] if kind != "date" else align["left"]
                if kind == "index":
                    cell.font, cell.alignment = muted_font, align["center"]
            elif kind == "url" and isinstance(value, str) and value.startswith(("https://", "http://")):
                cell.hyperlink = value
                cell.font, cell.alignment = link_font, align["left"]
            elif kind == "badge":
                tone = _VALUE_TONES.get(str(value).strip().lower())
                if tone:
                    cell.font, cell.fill = tone_styles[tone]
                else:
                    cell.font = body_font
                cell.alignment = align["center"]
            elif kind == "platform":
                cell.font = platform_fonts.get(str(value).strip().lower(), body_font)
                cell.alignment = align["left"]
            elif kind == "long_text":
                cell.font, cell.alignment = (strong_font if index == 0 else body_font), align["wrap"]
                lines = max(lines, min(3, math.ceil(_display_len(value, kind) / max(widths[index] * 1.15, 1))))
            else:
                cell.font = strong_font if index == 0 else body_font
                cell.alignment = align["left"]
        # Excel does not auto-size wrapped rows written by a library: size them (max 3 lines).
        ws.row_dimensions[row_index].height = 22 if lines == 1 else 15 * lines + 6

    # Navigation and printing
    first_is_name = kinds[0] in {"long_text", "text"} and len(columns) >= 6
    ws.freeze_panes = "B2" if first_is_name else "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{max(len(rows) + 1, 2)}"
    ws.sheet_view.showGridLines = False
    ws.sheet_view.zoomScale = 100
    ws.sheet_properties.tabColor = HEADER_FILL
    ws.print_title_rows = "1:1"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.oddFooter.center.text = "Page &P of &N"

    _about_sheet(book, sheet_name, len(rows), info)
    book.active = 0
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _about_sheet(book: Workbook, sheet_name: str, row_count: int, info: dict[str, object]) -> None:
    ws = book.create_sheet("About")
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 70
    ws["A1"] = "CreatorIntel export"
    ws["A1"].font = Font(name=FONT, size=16, bold=True, color=HEADER_FILL)
    ws.row_dimensions[1].height = 28
    generated = info.pop("Generated", None) or datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    details = {"Sheet": sheet_name, "Rows": f"{row_count:,}", "Generated": generated, **info}
    label_font = Font(name=FONT, size=11, bold=True, color="475569")
    value_font = Font(name=FONT, size=11, color=INK)
    for offset, (label, value) in enumerate(details.items(), start=3):
        ws.cell(row=offset, column=1, value=label).font = label_font
        cell = ws.cell(row=offset, column=2, value=ILLEGAL_CHARACTERS_RE.sub("", str(value)))
        _as_text(cell)
        cell.font = value_font
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    note_row = len(details) + 4
    ws.cell(row=note_row, column=1, value="Note").font = label_font
    note = ws.cell(row=note_row, column=2, value="N/A = not available from the platform's official API. "
                                                  "\"-\" = not enough history yet (needs two daily checks).")
    note.font = Font(name=FONT, size=11, color="64748B", italic=True)
    note.alignment = Alignment(wrap_text=True, vertical="top")
    ws.row_dimensions[note_row].height = 32
