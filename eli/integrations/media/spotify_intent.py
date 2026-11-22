"""Parse user phrasing into Spotify playback intents (no Web API)."""
from __future__ import annotations

import re

_PLAYLIST_RE = re.compile(
    r"^\s*(?:my|the|a)?\s*(?P<name>.+?)\s*(?:play\s*list|playlist)\s*$", re.I
)
_LIKED_SONGS_RE = re.compile(
    r"^(?:my\s+)?(?:liked(?:\s+(?:songs?|playlist|play\s*list))?|likes|favou?rites?)$",
    re.I,
)
_ALBUM_RE = re.compile(
    r"^\s*(?:the\s+)?(?P<name>.+?)\s+album\s*$", re.I
)
_ALBUM_MID_RE = re.compile(
    r"^\s*(?:the\s+)?(?P<artist>.+?)\s+album\s+(?P<name>.+?)\s*$", re.I
)
_LP_RE = re.compile(
    r"^\s*(?:the\s+)?(?P<name>.+?)\s+lp\s*$", re.I
)
_ALBUM_CMD_RE = re.compile(
    r"^(?:the\s+)?album\s+(.+)$", re.I
)
_ARTIST_SONGS_RE = re.compile(
    r"^(?:songs?|tracks?|music)\s+by\s+(?P<artist>.+)$", re.I
)
_CHAT_LOG_RE = re.compile(
    r"^(?:🧑\s*)?(?:You|ELI)\s*\[\d{1,2}:\d{2}(?::\d{2})?\]\s*:\s*",
    re.I,
)
_ON_SPOTIFY_TAIL_RE = re.compile(r"\s+on\s+spotify\s*$", re.I)


def sanitize_media_query(query: str) -> str:
    """Strip chat-log prefixes and trailing platform noise from a play query."""
    q = str(query or "").strip()
    for _ in range(3):
        q2 = _CHAT_LOG_RE.sub("", q).strip()
        if q2 == q:
            break
        q = q2
    q = _ON_SPOTIFY_TAIL_RE.sub("", q).strip()
    # "well play x": the words before the verb are not the title (command_splitter.after_lead). Only
    # before "play": a title may open on a lead and a verb ("so close", "just give me a reason").
    from eli.runtime.command_splitter import after_lead
    told = after_lead(q)
    if re.match(r"(?:ply|play)\s", told, re.I):
        q = told
    q = re.sub(r"^(?:(?:please|just|now|ok|okay|go\s+on|can\s+you|could\s+you|will\s+you)\s+)*(?:ply|play)\s+",
               "", q, flags=re.I).strip()
    return q


def playlist_name(query: str) -> str:
    m = _PLAYLIST_RE.match(str(query or ""))
    if not m:
        return ""
    name = (m.group("name") or "").strip(" .,:;-")
    return name if len(name) >= 2 else ""


def is_liked_songs(name: str) -> bool:
    return bool(_LIKED_SONGS_RE.match(str(name or "").strip()))


# ── what a play request asks for ─────────────────────────────────────────────────────────────────
#
# One reading of the request, done once: what kind of thing is wanted and its parts. The player gets
# the parts, never the raw words ("album" and "(the full album)" were searched as part of a title),
# and whether it worked is judged by the kind (an album by its album, a radio by its seed), not by
# the words of the request appearing in a track's title.

MUSIC_KINDS = frozenset(("liked", "playlist", "album", "artist", "similar", "genre", "track"))
VIDEO_KINDS = frozenset(("episode", "film"))

_FILLER = re.compile(r"\(?\s*\b(?:the\s+)?(?:full|whole|entire|complete)\s+(?P<what>album|lp|ep|record)\b\s*\)?"
                     r"|\(\s*\)|\b(?:in\s+full|the\s+whole\s+thing|all\s+of\s+it)\b", re.I)
_OWNS = r"(?:'s|’s)"
_SIMILAR = re.compile(
    r"^(?:(?:some|more|play)\s+)?(?:(?:songs?|music|tracks?|stuff|something|anything|more)\s+"
    r"(?:similar\s+to|like|in\s+the\s+style\s+of|along\s+the\s+lines\s+of|that\s+sounds?\s+like)"
    r"|similar\s+to)\s+(?P<seed>.+)$"
    r"|^(?:a\s+)?radio\s+(?:for|of|from|based\s+on)\s+(?P<seed2>.+)$"
    r"|^(?P<seed3>.+?)\s+radio$", re.I)
_ALBUM_FIRST = re.compile(r"^(?:the\s+)?(?:album|lp|ep)\s+(?P<name>.+?)(?:\s+by\s+(?P<artist>.+))?$", re.I)
_ALBUM_LAST = re.compile(r"^(?:the\s+)?(?P<name>.+?)\s+(?:album|lp|ep)(?:\s+by\s+(?P<artist>.+))?$", re.I)
_ALBUM_BY_LAST = re.compile(r"^(?:the\s+)?(?P<name>.+?)\s+by\s+(?P<artist>.+?)\s+(?:album|lp|ep)$", re.I)
_ALBUM_OWNED = re.compile(rf"^(?P<the>the\s+)?(?P<artist>.+?){_OWNS}?\s+(?:album|lp|ep)\s+(?P<name>.+)$", re.I)
_BY_ARTIST = re.compile(r"^(?:(?:some|any|the)\s+)?(?:songs?|tracks?|music|stuff|something|anything|hits)\s+"
                        r"(?:by|from)\s+(?P<artist>.+)$", re.I)
_MOOD_OF = re.compile(rf"^(?P<artist>.+?){_OWNS}\s+(?P<mood>.+?)\s+(?:songs|tracks|music|stuff)$", re.I)
_GENRE = re.compile(r"^(?:(?:some|any)\s+(?P<g1>.+)|something\s+(?P<g2>.+)"
                    r"|(?P<g3>.+?)\s+(?:music|songs|tunes|vibes))$", re.I)
_EPISODE = re.compile(r"\b(?:season|episode|s\d{1,2}\s*e\d{1,3}|ep\.?\s*\d+|(?:tv\s+)?(?:show|series))\b", re.I)
_FILM = re.compile(r"\b(?:movie|film)\b", re.I)
_BY = re.compile(r"^(?P<title>.+?)\s+by\s+(?P<artist>.+)$", re.I)


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip(" .,:;-\"'“”")


def _searchable_artist(artist: str) -> str:
    """The artist as Spotify should be searched for: "veridian's" is veridian. Said without the
    apostrophe ("veridians album ...") it may be possessive or the name itself, and the shorter form
    is the one a search still finds either way (search matches word beginnings)."""
    a = re.sub(rf"{_OWNS}$", "", _tidy(artist), flags=re.I)
    if a != _tidy(artist):
        return a
    return a[:-1] if len(a) > 2 and a.lower().endswith("s") and not a.lower().endswith("ss") else a


def _album_owner(said: str, *, named_with_the: bool) -> str:
    """Whose album "<said> album <name>" asks for. "veridians album ..." is Veridian's: the possessive
    said or typed without its apostrophe, so the artist is named without it. With "the" in front ("the
    copper tides album ox") the "s" is the name's own, a band named in the plural."""
    a = _tidy(said)
    if not named_with_the and len(a) > 2 and re.search(r"[^s]s$", a, re.I):
        return a[:-1]
    return a


def media_request(query: str, kind: str | None = None) -> dict:
    """What a play request asks for: {"kind", "title", "artist", "seed", "search"}.

    kind: liked | playlist | album | artist | similar (seed: what it should be like) | genre (a style
    or mood, title: the style) | episode | film | track (a title with its artist) | title (a bare
    title: nothing says whether it is a song, a show or a film). `kind` given by the caller (the
    router or the model) is taken as said. `search` is the words to give a player."""
    q = sanitize_media_query(query)
    album_said = False
    m = _FILLER.search(q)
    while m:
        album_said = album_said or bool(m.group("what"))
        q = _tidy(q[:m.start()] + " " + q[m.end():])
        m = _FILLER.search(q)
    q = _tidy(q)
    out = {"kind": "title", "title": q, "artist": "", "seed": "", "search": q}

    def frame(kind_, title="", artist="", seed=""):
        title, artist, seed = _tidy(title), _tidy(artist), _tidy(seed)
        search = seed or " ".join(x for x in (title, _searchable_artist(artist) if artist else "") if x)
        return {"kind": kind_, "title": title, "artist": artist, "seed": seed, "search": search or q}

    hint = str(kind or "").strip().lower()
    if not q:
        return out
    if is_liked_songs(q) or is_liked_songs(playlist_name(q)):
        return frame("liked", "Liked Songs")
    sm = _SIMILAR.match(q)
    if sm and hint in ("", "similar"):
        return frame("similar", seed=sm.group("seed") or sm.group("seed2") or sm.group("seed3"))
    pl = playlist_name(q)
    if pl and hint in ("", "playlist"):
        return frame("playlist", pl)
    for pattern in (_ALBUM_FIRST, _ALBUM_BY_LAST, _ALBUM_LAST, _ALBUM_OWNED):
        am = pattern.match(q)
        if am and len(_tidy(am.group("name"))) >= 2 and hint in ("", "album"):
            artist = am.groupdict().get("artist") or ""
            if pattern is _ALBUM_OWNED:
                artist = _album_owner(artist, named_with_the=bool(am.group("the")))
            return frame("album", am.group("name"), artist)
    if album_said or hint == "album":
        bm = _BY.match(q)
        return frame("album", bm.group("title"), bm.group("artist")) if bm else frame("album", q)
    am = _BY_ARTIST.match(q)
    if am:
        return frame("artist", artist=am.group("artist"))
    mm = _MOOD_OF.match(q)
    if mm:
        return {**frame("genre", mm.group("mood"), mm.group("artist")),
                "search": f"{_searchable_artist(mm.group('artist'))} {_tidy(mm.group('mood'))}"}
    if hint in ("episode", "film") or _EPISODE.search(q) or _FILM.search(q):
        found = hint if hint in ("episode", "film") else ("episode" if _EPISODE.search(q) else "film")
        return {**frame(found, q), "search": q}
    gm = _GENRE.match(q)
    if gm and hint in ("", "genre"):
        return frame("genre", gm.group("g1") or gm.group("g2") or gm.group("g3"))
    bm = _BY.match(q)
    if bm:
        return frame("track", bm.group("title"), bm.group("artist"))
    if hint in MUSIC_KINDS:
        return {**frame(hint, q), "search": q}
    return out


def spotify_searches(asked: dict) -> list[str]:
    """What to ask Spotify's search for a request read by media_request, best first: its words, then,
    for a song or an album, the same with Spotify's search filters ("track:" or "album:", and "artist:"),
    which hold the title to the song's or album's own name: the words alone started another of the
    artist's songs. What starts is judged either way (cross_platform.track_matches, album_matches)."""
    plain = str(asked.get("search") or "").strip()
    out = [plain] if plain else []
    field = {"track": "track", "album": "album"}.get(str(asked.get("kind") or ""))
    title, artist = _tidy(asked.get("title")), _tidy(asked.get("artist"))
    if field and title:
        filtered = f"{field}:{title}" + (f" artist:{_searchable_artist(artist)}" if artist else "")
        if filtered not in out:
            out.append(filtered)
    return out


def album_request(query: str) -> tuple[str, str | None]:
    """Return (album_name, artist_or_none) when the user asked for an album (media_request)."""
    asked = media_request(query)
    if asked["kind"] != "album":
        return "", None
    return asked["title"], (asked["artist"] or None)


def artist_songs_request(query: str) -> str:
    m = _ARTIST_SONGS_RE.match(str(query or "").strip())
    if not m:
        return ""
    artist = (m.group("artist") or "").strip(" .,:;-")
    return artist if len(artist) >= 2 else ""


def normalize_play_query(query: str) -> str:
    """Strip chat noise and leading 'the album' filler from a play request."""
    q = sanitize_media_query(query)
    q = re.sub(r"^(?:the\s+)?album\s+", "", q, flags=re.I).strip()
    return q


def youtube_search_query(query: str) -> str:
    """Turn natural album phrasing into a YouTube-friendly search string."""
    q = normalize_play_query(query)
    raw = str(query or "").lower()
    if not re.search(r"\balbum\b", raw) and not re.match(r"^\s*the\s+album\b", raw):
        return q
    by_m = re.match(r"^(.+?)\s+by\s+(.+)$", q, re.I)
    if by_m:
        title, artist = by_m.group(1).strip(), by_m.group(2).strip()
        title = re.sub(r"\s+album\s*$", "", title, flags=re.I).strip()
        if "album" not in title.lower():
            title = f"{title} album"
        return f"{artist} {title}"
    if "album" not in q.lower():
        q = f"{q} album"
    return q


def youtube_mpv_query(query: str, *, by_artist: tuple[str, str] | None = None) -> str:
    """Search string for headless mpv/yt-dlp — album-aware, otherwise plain."""
    if album_request(query)[0] or re.search(r"\balbum\b", str(query or ""), re.I):
        base = youtube_search_query(query)
        return base if "official audio" in base.lower() else f"{base} official audio"
    q = normalize_play_query(query)
    if by_artist:
        return f"{by_artist[0]} {by_artist[1]} official audio"
    return q


def prefers_spotify_music_context(query: str) -> bool:
    """True when phrasing is album/playlist/artist — not a bare song title."""
    q = str(query or "").strip().lower()
    if not q:
        return False
    if playlist_name(query) or is_liked_songs(playlist_name(query)):
        return True
    if album_request(query)[0]:
        return True
    if artist_songs_request(query):
        return True
    return bool(re.search(r"\b(?:album|playlist|play\s*list|liked\s+songs?)\b", q))
