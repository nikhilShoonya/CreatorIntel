"""HTTP fakes for tests only (served through httpx.MockTransport)."""

import json

import httpx

# ------------------------------------------------------------------ YouTube
YT_CHANNELS = {
    "@nitinnitro": {
        "id": "UCaaaaaaaaaaaaaaaaaaaaaa",
        "title": "Nitin Nitro",
        "description": "Stock market ka analysis Hindi aur English mein. Nifty, Bank Nifty.",
        "subscribers": "890000",
        "uploads": "UUaaaaaaaaaaaaaaaaaaaaaa",
    },
}
# 12 videos; newest first by index. Views 100k..1.2M
YT_VIDEOS = [
    {
        "id": f"vid{i:02d}",
        "title": f"Nifty Tomorrow Analysis part {i}",
        "publishedAt": f"2024-09-{28 - i:02d}T10:00:00Z",
        "views": str(100_000 * (i + 1)),
        "likes": str(4_000 * (i + 1)),
        "comments": str(1_000 * (i + 1)),
    }
    for i in range(12)
]


def youtube_handler(request: httpx.Request) -> httpx.Response:
    assert request.headers.get("X-Goog-Api-Key") == "test-youtube-key"
    assert "key=" not in str(request.url), "API key must not be sent in the URL"
    path = request.url.path.rsplit("/", 1)[-1]
    params = request.url.params
    if path == "search":
        raise AssertionError("search.list must not be used")
    if path == "channels":
        handle = params.get("forHandle", "").lower()
        channel = YT_CHANNELS.get(handle)
        if channel is None:
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, json={"items": [{
            "id": channel["id"],
            "snippet": {"title": channel["title"], "description": channel["description"]},
            "statistics": {"subscriberCount": channel["subscribers"], "hiddenSubscriberCount": False},
            "contentDetails": {"relatedPlaylists": {"uploads": channel["uploads"]}},
        }]})
    if path == "playlistItems":
        return httpx.Response(200, json={"items": [{"contentDetails": {"videoId": v["id"]}} for v in YT_VIDEOS]})
    if path == "videos":
        ids = params.get("id", "").split(",")
        items = [
            {
                "id": v["id"],
                "snippet": {"title": v["title"], "description": "Market ka next move kya hoga?", "publishedAt": v["publishedAt"],
                            "tags": ["nifty", "stock market"], "liveBroadcastContent": "none"},
                "statistics": {"viewCount": v["views"], "likeCount": v["likes"], "commentCount": v["comments"]},
                "contentDetails": {"duration": "PT10M"},
            }
            for v in YT_VIDEOS if v["id"] in ids
        ]
        return httpx.Response(200, json={"items": items})
    return httpx.Response(404, json={"error": {"code": 404, "message": "not found"}})


# ---------------------------------------------------------------- Instagram
IG_PROFESSIONAL = {
    "tradingtech31": {
        "followers_count": 1_200_000,
        "media": [
            {
                "id": f"m{i}", "caption": f"Bank Nifty Analysis day {i} #banknifty #trading",
                "media_type": "VIDEO", "media_product_type": "REELS",
                "permalink": f"https://www.instagram.com/reel/abc{i}/",
                "timestamp": f"2024-09-{28 - i:02d}T10:00:00+0000",
                "like_count": 10_000, "comments_count": 500, "view_count": 200_000 + 10_000 * i,
            }
            for i in range(12)
        ],
    },
}


def instagram_handler(request: httpx.Request) -> httpx.Response:
    assert request.headers.get("Authorization") == "Bearer test-meta-token"
    assert "access_token" not in str(request.url)
    assert request.url.params.get("appsecret_proof")
    fields = request.url.params.get("fields", "")
    username = fields.split("username(", 1)[1].split(")", 1)[0] if "username(" in fields else ""
    account = IG_PROFESSIONAL.get(username)
    if account is None:
        return httpx.Response(400, json={"error": {
            "message": "Invalid user id", "type": "OAuthException", "code": 110, "error_subcode": 2207013,
        }})
    media = [dict(m) for m in account["media"]]
    if "view_count" not in fields:
        for m in media:
            m.pop("view_count", None)
    return httpx.Response(200, json={"business_discovery": {
        "id": "178400001", "username": username, "name": username.title(),
        "biography": "Daily Nifty & Bank Nifty trading setups", "followers_count": account["followers_count"],
        "media_count": len(media), "media": {"data": media},
    }, "id": "17840000000000000"})


# ---------------------------------------------------------------------- LLM
def llm_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    result = {
        "genre": "Finance & Investment",
        "sub_genre": "Stock Market & Trading",
        "language": "Hinglish",
        "secondary_language": "Hindi",
        "sentiment": "Positive",
        "sentiment_score": 0.6,
        "genre_confidence": 0.95,
        "language_confidence": 0.9,
        "sentiment_confidence": 0.8,
        "evidence_topics": ["Nifty", "Bank Nifty"],
    }
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": json.dumps(result)}}]})


def mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))
