"""Agent 2 - Platform Resolver.

Decides YouTube / Instagram / Unsupported / Invalid from the URL only
(never from the channel name) and produces the normalised identifier.
"""

from app.utils.url_parser import ParsedLink, parse_channel_link


class PlatformResolver:
    def resolve(self, channel_link: object) -> ParsedLink:
        return parse_channel_link(channel_link)
