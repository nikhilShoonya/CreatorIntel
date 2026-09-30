"""Video Performance - an independent module: per-video daily view tracking and new-video discovery.

It shares only infrastructure with the rest of the app (settings/.env, HTTP client,
LLM client, DB session). All data lives in its own ``video_tracking_*`` tables with
no foreign keys to the Creator Analytics tables.
"""
