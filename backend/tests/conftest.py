"""Test configuration.

Tests never call real external APIs: platform/LLM HTTP traffic is served by
httpx.MockTransport handlers defined in tests/fakes.py. Environment values
set here take precedence over backend/.env.
"""

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="creatorintel-tests-"))
os.environ.update(
    {
        # Set CREATORINTEL_TEST_DATABASE_URL to run the suite against PostgreSQL instead.
        "DATABASE_URL": os.environ.get("CREATORINTEL_TEST_DATABASE_URL") or f"sqlite:///{(_TMP / 'test.db').as_posix()}",
        "UPLOAD_DIR": str(_TMP / "uploads"),
        "LOG_DIR": str(_TMP / "logs"),
        "YOUTUBE_API_KEY": "test-youtube-key",
        "META_ACCESS_TOKEN": "test-meta-token",
        "META_APP_SECRET": "test-app-secret",
        "META_IG_BUSINESS_ACCOUNT_ID": "17840000000000000",
        "GROQ_API_KEY": "test-groq-key",
        # never pick up real numbered keys from backend/.env in tests
        "GROQ_API_KEY_1": "",
        "GROQ_API_KEY_2": "",
        "GROQ_API_KEY_3": "",
        # No real Groq limits in tests (mocked responses); pacing itself is tested in test_llm_rate_limits.py.
        "GROQ_REQUESTS_PER_MINUTE": "100000",
        "GROQ_TOKENS_PER_MINUTE": "100000000",
        "HTTP_MAX_RETRIES": "1",
        "CACHE_TTL_HOURS": "24",
        "VIDEO_TRACKING_SCHEDULER_ENABLED": "false",
    }
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
