import io

import pandas as pd
import pytest

from app.agents.ingestion_agent import FileIngestionAgent, IngestionError, sanitize_filename


def _xlsx(rows: list[dict]) -> bytes:
    buffer = io.BytesIO()
    pd.DataFrame(rows).to_excel(buffer, index=False)
    return buffer.getvalue()


def test_csv_with_aliases_duplicates_and_invalid_rows():
    content = (
        "Creator Name,Creator Link\n"
        "Trading Tech,https://www.instagram.com/tradingtech31/\n"
        "Trading Tech again,instagram.com/TradingTech31/reels/\n"
        "Nitin Nitro,https://www.youtube.com/@nitinnitro\n"
        "Broken,not a link\n"
        ",\n"
        "Other,https://twitter.com/x\n"
    ).encode()
    agent = FileIngestionAgent()
    result = agent.ingest("list.csv", content)
    assert result.total_input_rows == 5
    assert result.duplicate_rows == 1
    platforms = [row.platform for row in result.rows]
    assert platforms == ["instagram", "youtube", "invalid", "unsupported"]
    assert result.rows[0].source_row == 2
    assert result.rows[2].error


def test_xlsx_with_snake_case_headers():
    content = _xlsx([
        {"channel_name": "A", "channel_link": "https://www.youtube.com/@abc"},
        {"channel_name": "B", "channel_link": "https://www.instagram.com/bbb/"},
    ])
    agent = FileIngestionAgent()
    assert agent.validate_file("Creators.xlsx", content, 10 * 1024 * 1024) == "Creators.xlsx"
    result = agent.ingest("Creators.xlsx", content)
    assert [r.identifier for r in result.rows] == ["abc", "bbb"]


def test_missing_columns_rejected():
    agent = FileIngestionAgent()
    with pytest.raises(IngestionError, match="Channel Link"):
        agent.ingest("x.csv", b"Channel Name,Followers\nA,100\n")


def test_file_validation():
    agent = FileIngestionAgent()
    with pytest.raises(IngestionError, match="Unsupported file type"):
        agent.validate_file("evil.exe", b"MZ", 1000)
    with pytest.raises(IngestionError, match="not a valid Excel"):
        agent.validate_file("fake.xlsx", b"hello", 1000)
    with pytest.raises(IngestionError, match="larger than"):
        agent.validate_file("big.csv", b"a" * 2_000_000, 1024 * 1024)
    assert sanitize_filename("../../etc/pass wd<>.CSV") == "pass wd.csv"
