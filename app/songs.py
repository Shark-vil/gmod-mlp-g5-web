"""Dance songs from players (the mod's PLANS.md 13): upload, analysis, the editor's copy, the published copy,
the song mod, the audit.

A logged-in player uploads a song (mp3 / ogg / wav / flac / m4a / opus; MLP_SONG_MAX_MB, MLP_SONG_MAX_SEC);
the server converts it to ogg without metadata (ffmpeg) — kept for good, so the author can always come back
and edit without uploading again — finds the tempo and makes a first note chart (chart_auto.py, in the
background, one song at a time) and a preview fragment.

Every song has two copies:
  the working copy — what the author edits: status draft -> built (the mod is made) -> pending (the Workshop
                     link is in, waiting for the audit) -> approved / rejected
  the published copy (live_*) — what an admin approved last: shown on /songs, used by the game servers. Editing
                     an approved song ("revise") doesn't touch it until the new version is approved too.
The content filter (the admins' ban words) checks the title, the artist, the tags, the source link and the
Workshop item's title / description: a song that hits it can't be sent for review, and a published one that hits a
word added later is hidden (not deleted). An author's nick that hits it only shows as a "barcode".
Tags (the author's, reviewed), genres (the admins' list, no review), the source link (reviewed), the age marks
(swearing: some / over 20 %, a sexual subtext -> 16+ / 18+; raising one is at once, lowering it is reviewed).
Trust: an author whose songs pass the review gets points; a trusted one's updates of published songs are
approved by themselves (the filter still checks them).

  POST   /api/v1/songs                  multipart: file, title, artist   -> { uuid }
  GET    /api/v1/songs                  the published songs (?q= search, ?limit, ?offset)
  GET    /api/v1/songs/game             for the game servers: the published songs with notes, keys, Workshop item, hashes
  GET    /api/v1/songs/mine             the player's own songs
  GET    /api/v1/songs/{uuid}           the song: published copy for everybody, working copy + beat grid for the author / admins
  GET    /api/v1/songs/{uuid}/full.ogg  the full track (author / admins)
  GET    /api/v1/songs/{uuid}/preview.ogg   the published fragment (?work=1: the working one, author / admins)
  PUT    /api/v1/songs/{uuid}/chart     the editor's save (author, draft): notes, holds (+ "lens": seconds per note), keys, preview, title
  POST   /api/v1/songs/{uuid}/trim      { start, end } cut the song out of the uploaded source (author, draft); the notes move with it
  GET    /api/v1/songs/{uuid}/cover.jpg the published cover (?work=1: the working one), or the default picture
  POST   /api/v1/songs/{uuid}/cover     multipart image: the author's own cover (512x512 jpg); DELETE: back to the file's own / none
  POST   /api/v1/songs/{uuid}/auto      redo the automatic chart (author, draft)
  POST   /api/v1/songs/{uuid}/build     the working copy -> the song mod: status "built", SHA256 of its files kept
  GET    /api/v1/songs/{uuid}/mod.zip   the song mod (author / admins)
  POST   /api/v1/songs/{uuid}/unbuild   built -> draft
  POST   /api/v1/songs/{uuid}/submit    { workshop } -> checked with Steam -> pending; a draft of a published song that
                                        only changes the title / artist / cover / preview (meta_only) goes with no build
  POST   /api/v1/songs/{uuid}/revise    approved / rejected -> draft (a new version; the published one stays)
  GET    /api/v1/genres                 the genres { id, ru, en }; GET /api/v1/tags?q= the published songs' tags (hints)
  DELETE /api/v1/songs/{uuid}           the author (never published) or an admin
  admin: GET /api/v1/admin/songs (?status, ?q), POST …/{uuid}/approve, POST …/{uuid}/reject { reason },
         GET / PUT /api/v1/admin/banwords { words }, POST /api/v1/admin/users/{steamid}/ban { banned, reason },
         GET / PUT /api/v1/admin/genres { genres: [ { id?, ru, en } ] }, POST /api/v1/admin/users/{steamid}/trust { trusted }
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid as uuidlib
import zipfile

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

try:
    from . import auth, chart_auto
except ImportError:
    import auth
    import chart_auto

# 40 MB: 5 minutes of FLAC (~25-35 MB) or of a 320 kbps mp3 (~12 MB) fit, a 16-bit WAV up to ~3.7 min; no 1 GB files
MAX_MB = float(os.environ.get("MLP_SONG_MAX_MB", "40"))
MAX_SEC = float(os.environ.get("MLP_SONG_MAX_SEC", "300"))
MIN_SEC = 20
MAX_DRAFTS = int(os.environ.get("MLP_SONG_MAX_DRAFTS", "5"))
UPLOADS_PER_HOUR = int(os.environ.get("MLP_SONG_UPLOADS_PER_HOUR", "10"))
PREVIEW_LEN = 20
EXTS = {".mp3", ".ogg", ".wav", ".flac", ".m4a", ".opus"}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
SITE = os.environ.get("MLP_PUBLIC_URL", "").rstrip("/")
HOLD_LEN = 1.94      # a hold with no length of its own lasts 4 of the game's beats (sh_mg_dance.lua D.HOLD)
HOLD_MIN, HOLD_MAX = 0.5, 8.0  # a hold's own length (the chart's "lens": seconds per note, 0 for a tap)


def hold_len(lens, i):
    """the length of the hold at index i (0-based) of a chart"""
    v = lens[i] if lens and i < len(lens) else 0
    return v if v and v > 0 else HOLD_LEN
MIN_NOTE_GAP = 0.25  # closer, one press would count for two notes (the GOOD window is ±0.2 s)

router = APIRouter()
_db = None
_dir = None
_work = threading.Semaphore(1)  # one analysis at a time (librosa is heavy)

KEYS = ("", "up", "down", "left", "right", "act")  # a note's key: "" = random (a new one every game), like the game's own
TRIM_FADE = 0.4  # seconds of fade at a cut edge (no click)
COVER_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}  # a gif: its first frame
ICON_MAX = 1024 * 1024  # the Workshop icon (addon.jpg): 512x512, at most 1 MB
COVER_MAX_MB = 5
DEFAULT_COVER = os.path.join(os.path.dirname(__file__), "static", "song_icon.jpg")
LIVE_COLS = {"trim_start": "REAL DEFAULT 0", "trim_end": "REAL", "src_len": "REAL", "live_title": "TEXT", "live_artist": "TEXT", "live_chart": "TEXT", "live_hashes": "TEXT", "live_wsid": "TEXT",
             "live_len": "REAL", "live_beat": "REAL", "live_version": "INTEGER DEFAULT 0", "live_at": "INTEGER", "version": "INTEGER DEFAULT 0",
             # tags / the source link / the age marks: the working copy and the published one; genres: one copy (no review)
             "tags": "TEXT", "live_tags": "TEXT", "genres": "TEXT", "source": "TEXT", "live_source": "TEXT",
             "swear": "INTEGER DEFAULT 0", "lewd": "INTEGER DEFAULT 0", "live_swear": "INTEGER DEFAULT 0", "live_lewd": "INTEGER DEFAULT 0",
             "ws_title": "TEXT", "ws_desc": "TEXT",            # the Workshop item's title / description (at the submit)
             "approved_by": "TEXT", "filter_at": "INTEGER DEFAULT 0"}  # "auto" / the admin; the last filter penalty
USER_COLS = {"trust": "INTEGER DEFAULT 0", "trusted": "INTEGER DEFAULT 0"}


def init(db_factory, data_dir):
    global _db, _dir
    _db, _dir = db_factory, os.path.join(data_dir, "songs")
    os.makedirs(_dir, exist_ok=True)
    with _db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS songs ( uuid TEXT PRIMARY KEY, owner TEXT, title TEXT, artist TEXT, status TEXT,
                created INTEGER, updated INTEGER, len REAL, bpm REAL, beat REAL, beats TEXT, chart TEXT,
                preview_start REAL, preview_len REAL, wsid TEXT, hashes TEXT, reason TEXT,
                analysis TEXT, analysis_error TEXT );
            CREATE INDEX IF NOT EXISTS idx_songs_owner ON songs ( owner );
            CREATE INDEX IF NOT EXISTS idx_songs_status ON songs ( status );
            CREATE TABLE IF NOT EXISTS meta ( key TEXT PRIMARY KEY, value TEXT );
            """
        )
        have = {r["name"] for r in con.execute("PRAGMA table_info( songs )")}
        for col, kind in LIVE_COLS.items():  # the published copy (added after the first version)
            if col not in have:
                con.execute(f"ALTER TABLE songs ADD COLUMN {col} {kind}")
        have = {r["name"] for r in con.execute("PRAGMA table_info( users )")}
        for col, kind in USER_COLS.items():  # the author's trust
            if col not in have:
                con.execute(f"ALTER TABLE users ADD COLUMN {col} {kind}")


def song_dir(u):
    return os.path.join(_dir, u)


def _now():
    return int(time.time())


def _ffprobe_len(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, timeout=60)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def _ffmpeg(args):
    r = subprocess.run(["ffmpeg", "-v", "error", "-y"] + args, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "ffmpeg failed")[-300:])


def to_cover(src, out, stream=None):
    """any picture (or an audio file's attached picture) -> a 512x512 jpg; True if it worked"""
    args = ["-i", src] + (["-map", stream] if stream else []) + ["-frames:v", "1", "-vf",
            "scale=512:512:force_original_aspect_ratio=increase,crop=512:512", "-q:v", "3", out]
    try:
        _ffmpeg(args)
        return os.path.exists(out) and os.path.getsize(out) > 0
    except Exception:
        if os.path.exists(out):
            os.remove(out)
        return False


def addon_icon(src):
    """the mod's Workshop icon: a 512x512 jpg of at most 1 MB (the Steam / gmpublish rules), from any cover"""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "addon.jpg")
        for q in (3, 6, 10, 16, 24, 31):  # the jpg quality steps down until it fits
            _ffmpeg(["-i", src, "-frames:v", "1", "-vf", "scale=512:512:force_original_aspect_ratio=increase,crop=512:512",
                     "-q:v", str(q), out])
            if os.path.getsize(out) <= ICON_MAX:
                with open(out, "rb") as f:
                    return f.read()
    raise RuntimeError("the icon doesn't fit in 1 MB")


def cover_path(u, live=False):
    p = os.path.join(song_dir(u), "cover_live.jpg" if live else "cover.jpg")
    return p if os.path.exists(p) else None


def make_preview(u, start, length):
    """the working copy's fragment: length seconds from start, a short fade in / out"""
    full = os.path.join(song_dir(u), "full.ogg")
    out = os.path.join(song_dir(u), "preview.ogg")
    fade = min(1.0, length / 4)
    _ffmpeg(["-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", full, "-af",
             f"afade=t=in:d={fade:.2f},afade=t=out:st={max(0, length - fade):.2f}:d={fade:.2f}",
             "-map_metadata", "-1", "-c:a", "libvorbis", "-q:a", "3", out])


def _analyse(u, notes=True):
    """the tempo, the beat grid, the preview; the automatic notes only when asked (notes=True: the editor's button)"""
    with _work:
        with _db() as con:
            con.execute("UPDATE songs SET analysis = 'running', updated = ? WHERE uuid = ?", (_now(), u))
        try:
            res = chart_auto.analyze(os.path.join(song_dir(u), "full.ogg"))
            start = round(max(0.0, min(res["len"] - PREVIEW_LEN, res["len"] * 0.3)), 2)
            length = round(min(PREVIEW_LEN, res["len"]), 2)
            with _db() as con:
                r = con.execute("SELECT preview_start, preview_len, chart FROM songs WHERE uuid = ?", (u,)).fetchone()
            make_preview(u, r["preview_start"] if r and r["preview_start"] is not None else start,
                         r["preview_len"] if r and r["preview_len"] else length)
            if not notes:  # a new upload starts with no notes; the author places them (or asks for the automatic ones)
                res["chart"] = json.loads(r["chart"]) if r and r["chart"] else {"t": [], "large": []}
            with _db() as con:
                con.execute("UPDATE songs SET analysis = 'done', analysis_error = NULL, len = ?, bpm = ?, beat = ?, beats = ?, chart = ?, "
                            "preview_start = COALESCE( preview_start, ? ), preview_len = COALESCE( preview_len, ? ), updated = ? WHERE uuid = ?",
                            (res["len"], res["bpm"], res["beat"], json.dumps(res["beats"]), json.dumps(res["chart"]), start, length, _now(), u))
        except Exception as e:
            with _db() as con:
                con.execute("UPDATE songs SET analysis = 'failed', analysis_error = ?, updated = ? WHERE uuid = ?", (str(e)[:300], _now(), u))
        finally:
            # deleted while it was being analysed: clean up after ourselves (the files were in use then)
            with _db() as con:
                r = con.execute("SELECT status FROM songs WHERE uuid = ?", (u,)).fetchone()
            if r and r["status"] == "deleted":
                shutil.rmtree(song_dir(u), ignore_errors=True)


def start_analysis(u, notes=True):
    threading.Thread(target=_analyse, args=(u, notes), daemon=True).start()


# ---------------------------------------------------------------------------------------------- the content filter
_WORDS = {"t": 0, "words": []}


def ban_words():
    if time.time() - _WORDS["t"] < 5:   # a list of songs asks for every row: read once in a while
        return _WORDS["words"]
    with _db() as con:
        r = con.execute("SELECT value FROM meta WHERE key = 'ban_words'").fetchone()
    try:
        words = json.loads(r["value"]) if r else []
    except Exception:
        words = []
    _WORDS.update(t=time.time(), words=words)
    return words


def filter_hits(fields, words=None):
    """[ ( where, text ) ] -> [ { word, where } ]: every ban word found (a part of a word counts too)"""
    words = ban_words() if words is None else words
    out = []
    for where, text in fields:
        low = (text or "").lower()
        if not low:
            continue
        for w in words:
            if w and w.lower() in low:
                out.append({"word": w, "where": where})
    return out


def song_fields(r, live, ws=None):
    """what the filter reads in a song: the working copy or the published one, and the Workshop item"""
    p = "live_" if live else ""
    tags = _json(r[p + "tags"], [])
    title, desc = (ws.get("title"), ws.get("description")) if ws else (r["ws_title"], r["ws_desc"])
    return [("title", r[p + "title"]), ("artist", r[p + "artist"]), ("tags", " | ".join(tags)), ("source", r[p + "source"]),
            ("ws_title", title), ("ws_desc", desc)]


def hidden(r, words=None):
    """the published copy hits the filter (a word added after it was approved)"""
    return bool(r["live_version"]) and bool(filter_hits(song_fields(r, True), words))


def nick_hidden(nick, words=None):
    return bool(filter_hits([("nick", nick)], words))


def barcode(steamid):
    """a nick that hits the filter, for the game: bars made from the SteamID's digits (the same author, the same bars)"""
    return " ".join("|" * (1 + int(d) % 4) for d in str(steamid or "0")[-6:])


def _json(v, empty):
    try:
        return json.loads(v) if v else empty
    except Exception:
        return empty


# the age marks: swear 0 none / 1 some / 2 over 20 % of the words; lewd 0 / 1 (a sexual subtext)
def age_of(swear, lewd):
    swear, lewd = int(swear or 0), int(lewd or 0)
    if swear >= 2 or (swear and lewd):
        return 18
    return 16 if swear or lewd else 0


# tags: lower case, letters / digits / spaces / - / _, 2-30 characters, at most 10 (a "#" goes)
TAG_MIN, TAG_LEN, TAG_MAX = 2, 30, 10
_TAG_BAD = re.compile(r"[^\w \-]", re.UNICODE)


def clean_tags(v):
    out = []
    for t in (v if isinstance(v, list) else []):
        t = _TAG_BAD.sub("", str(t).replace("#", " ").lower())
        t = re.sub(r"\s+", " ", t).strip()
        if TAG_MIN <= len(t) <= TAG_LEN and t not in out:
            out.append(t)
    return out[:TAG_MAX]


def clean_source(v):
    v = str(v or "").strip()[:300]
    if not v:
        return ""
    p = urllib.parse.urlparse(v)
    if p.scheme not in ("http", "https") or not p.netloc or " " in v:
        raise HTTPException(400, "the source must be an http(s) link")
    return v


# genres: the admins' list (meta 'genres'), a song has 1-3 of them by id; a renamed genre keeps its id
GENRE_MAX = 3
DEFAULT_GENRES = [("pop", "Поп", "Pop"), ("rock", "Рок", "Rock"), ("metal", "Метал", "Metal"), ("electronic", "Электроника", "Electronic"),
                  ("dance", "Танцевальная", "Dance"), ("chiptune", "Чиптюн / 8-bit", "Chiptune / 8-bit"), ("hiphop", "Хип-хоп", "Hip-hop"),
                  ("jazz", "Джаз", "Jazz"), ("classical", "Классика", "Classical"), ("soundtrack", "Оркестр / саундтрек", "Orchestral / soundtrack"),
                  ("folk", "Фолк / акустика", "Folk / acoustic"), ("jpop", "J-pop / K-pop", "J-pop / K-pop"), ("mlp", "Песни из MLP", "MLP songs"),
                  ("fan", "Фанатские песни (брони)", "Fan songs (brony)"), ("remix", "Ремикс / мэшап", "Remix / mashup"), ("other", "Другое", "Other")]


def genres_list():
    with _db() as con:
        r = con.execute("SELECT value FROM meta WHERE key = 'genres'").fetchone()
    if not r:
        return [{"id": i, "ru": ru, "en": en} for i, ru, en in DEFAULT_GENRES]
    return _json(r["value"], [])


def clean_genres(v, known=None):
    ids = {g["id"] for g in (known if known is not None else genres_list())}
    out = []
    for g in (v if isinstance(v, list) else []):
        g = str(g)
        if g in ids and g not in out:
            out.append(g)
    return out[:GENRE_MAX]


# trust: +1 an approval by an admin, -3 a rejection, -1 a filter refusal (once a day per song); trusted from
# TRUST_AT points, the mark goes below TRUST_KEEP (one slip doesn't take it away)
TRUST_AT, TRUST_KEEP = 5, 2
TRUST_APPROVE, TRUST_REJECT, TRUST_FILTER = 1, -3, -1


def add_trust(con, steamid, delta):
    r = con.execute("SELECT trust, trusted FROM users WHERE steamid = ?", (steamid,)).fetchone()
    if not r:
        return
    pts = max(-20, min(50, (r["trust"] or 0) + delta))
    trusted = 1 if pts >= TRUST_AT else (0 if pts < TRUST_KEEP else (r["trusted"] or 0))
    con.execute("UPDATE users SET trust = ?, trusted = ? WHERE steamid = ?", (pts, trusted, steamid))


# ---------------------------------------------------------------------------------------------- reading
def _row(u):
    if not UUID_RE.match(u or ""):
        raise HTTPException(404, "no such song")
    with _db() as con:
        r = con.execute("SELECT s.*, u.nick AS owner_nick, u.banned AS owner_banned, u.trusted AS owner_trusted, u.trust AS owner_trust FROM songs s LEFT JOIN users u ON u.steamid = s.owner "
                        "WHERE s.uuid = ?", (u,)).fetchone()
    if not r or r["status"] == "deleted":
        raise HTTPException(404, "no such song")
    return r


def _can_edit(request, r):
    u = auth.current_user(request)
    return bool(u and (u["admin"] or u["steamid"] == r["owner"]))


def _workshop(wsid):
    return f"https://steamcommunity.com/sharedfiles/filedetails/?id={wsid}" if wsid else None


# ---------------------------------------------------------------------------------------------- best scores per song
# the mod keeps best_dance_<up to 12 letters / digits of the song's uuid> (sh_mg_dance.lua D.SongStatKey); a score
# above what the song's notes can give is a cheat (main.py flags it): every note perfect at the top combo (9) plus
# every hold's ticks (a quarter of the game's beat, 0.485 s, for every song: sh_mg_dance.lua D.TICK), with a margin. The game's own songs have fixed caps; any other key
# (an installed song mod the site doesn't know) gets the most a song can hold.
DANCE_TOP, DANCE_TICK, BUILTIN_CAPS, ANY_SONG_CAP = 9, 0.485 / 4, {"best_dance_game": 1500, "best_dance_wwu": 1700}, 30000  # wwu: built in before
_CAPS = {"t": 0, "caps": {}}


def song_stat(u):
    return "best_dance_" + re.sub(r"[^a-z0-9]", "", u.lower())[:12]


def chart_cap(chart):
    tick = DANCE_TICK
    large = set(chart.get("large") or [])
    lens = chart.get("lens") or []
    pts = 0
    for i, _ in enumerate(chart.get("t") or []):
        pts += int(hold_len(lens, i) / tick) + 1 if (i + 1) in large else DANCE_TOP
    return int(pts * 1.1) + 50


def best_cap(key):
    """the most a best_dance* key may hold (None: not a dance score)"""
    if key != "best_dance" and not key.startswith("best_dance_"):
        return None
    if time.time() - _CAPS["t"] > 300:
        caps = {}
        with _db() as con:
            for r in con.execute("SELECT uuid, live_chart FROM songs WHERE live_version > 0 AND live_chart IS NOT NULL AND status != 'deleted'"):
                caps[song_stat(r["uuid"])] = chart_cap(json.loads(r["live_chart"]))
        _CAPS.update(t=time.time(), caps=caps)
    caps = {**_CAPS["caps"], **BUILTIN_CAPS}
    if key == "best_dance":
        return max(caps.values())
    return caps.get(key, ANY_SONG_CAP)


def _owner(r, words=None):
    """the author as the public sees them: the nick (or a barcode, when it hits the filter), the trust mark"""
    hid = nick_hidden(r["owner_nick"], words)
    return {"owner": r["owner"], "owner_nick": None if hid else r["owner_nick"], "owner_nick_hidden": hid,
            "owner_profile": f"https://steamcommunity.com/profiles/{r['owner']}", "owner_trusted": bool(r["owner_trusted"])}


SEL = "SELECT s.*, u.nick AS owner_nick, u.banned AS owner_banned, u.trusted AS owner_trusted, u.trust AS owner_trust FROM songs s LEFT JOIN users u ON u.steamid = s.owner"


def published(r, words=None):
    """the published copy (what the public and the game servers get), or None"""
    if not r["live_version"]:
        return None
    swear, lewd = r["live_swear"] or 0, r["live_lewd"] or 0
    return {"uuid": r["uuid"], "title": r["live_title"], "artist": r["live_artist"] or "", **_owner(r, words),
            "tags": _json(r["live_tags"], []), "genres": _json(r["genres"], []), "source": r["live_source"] or "",
            "swear": swear, "lewd": lewd, "age": age_of(swear, lewd),
            "len": r["live_len"], "beat": r["live_beat"], "version": r["live_version"], "approved_at": r["live_at"],
            "chart": json.loads(r["live_chart"]) if r["live_chart"] else None,
            "hashes": json.loads(r["live_hashes"]) if r["live_hashes"] else None,  # the game checks the downloaded mod against these
            "wsid": r["live_wsid"], "workshop": _workshop(r["live_wsid"]),
            "preview": f"/api/v1/songs/{r['uuid']}/preview.ogg", "page": f"/songs/{r['uuid']}",
            "cover": f"/api/v1/songs/{r['uuid']}/cover.jpg?v={r['live_version']}",
            "stat": song_stat(r["uuid"])}  # the best scores on it: GET /api/v1/leaderboard?stat=…


_SHA = {}  # path -> ( mtime, size, sha256 ): the files' hashes, worked out once per change


def _file_sha(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    k = (st.st_mtime_ns, st.st_size)
    if _SHA.get(path, (None,))[:2] != k:
        with open(path, "rb") as f:
            _SHA[path] = (*k, hashlib.sha256(f.read()).hexdigest())
    return _SHA[path][2]


def meta_changes(r):
    """a new version of a published song that only changes what the site shows (title, artist, cover, preview):
    the published mod still fits it (the game takes the title and the artist from the site, and checks the mod's
    files by the published SHA256), so it goes for the audit with no new build / upload.
    -> ( True, [ what changed… ] ) or ( False, [] ) when the notes, the sound or the length changed (or nothing to compare)"""
    u = r["uuid"]
    if not r["live_version"] or not r["live_hashes"] or not r["live_wsid"] or not r["chart"] or not r["live_chart"]:
        return False, []
    live_hashes = json.loads(r["live_hashes"])
    ogg = f"sound/mlp_g5/songs/{u}.ogg"
    if json.loads(r["chart"]) != json.loads(r["live_chart"]) or r["len"] != r["live_len"] or r["beat"] != r["live_beat"]:
        return False, []
    if _file_sha(os.path.join(song_dir(u), "full.ogg")) != live_hashes.get(ogg):
        return False, []
    d = song_dir(u)
    changes = [k for k, a, b in (("title", r["title"], r["live_title"]), ("artist", r["artist"] or "", r["live_artist"] or ""),
                                 ("tags", _json(r["tags"], []), _json(r["live_tags"], [])), ("source", r["source"] or "", r["live_source"] or ""),
                                 ("age", (r["swear"] or 0, r["lewd"] or 0), (r["live_swear"] or 0, r["live_lewd"] or 0))) if a != b]
    if _file_sha(os.path.join(d, "cover.jpg")) != _file_sha(os.path.join(d, "cover_live.jpg")):
        changes.append("cover")
    if _file_sha(os.path.join(d, "preview.ogg")) != _file_sha(os.path.join(d, "preview_live.ogg")):
        changes.append("preview")
    return True, changes


def working(r):
    """the working copy, for the author / admins"""
    return {"uuid": r["uuid"], "title": r["title"], "artist": r["artist"], "status": r["status"], **_owner(r),
            "owner_real_nick": r["owner_nick"],   # the author / admins see their own nick
            "tags": _json(r["tags"], []), "genres": _json(r["genres"], []), "source": r["source"] or "",
            "swear": r["swear"] or 0, "lewd": r["lewd"] or 0, "age": age_of(r["swear"], r["lewd"]), "approved_by": r["approved_by"],
            "len": r["len"], "bpm": r["bpm"], "beat": r["beat"], "preview_start": r["preview_start"], "preview_len": r["preview_len"],
            "wsid": r["wsid"], "workshop": _workshop(r["wsid"]), "analysis": r["analysis"], "analysis_error": r["analysis_error"],
            "created": r["created"], "updated": r["updated"], "reason": r["reason"], "version": r["version"] or 0,
            "chart": json.loads(r["chart"]) if r["chart"] else None,
            "hashes": json.loads(r["hashes"]) if r["hashes"] else None,
            "beats": json.loads(r["beats"]) if r["beats"] else [],
            "has_full": os.path.exists(os.path.join(song_dir(r["uuid"]), "full.ogg")),
            "trim_start": r["trim_start"] or 0, "trim_end": r["trim_end"], "src_len": r["src_len"] or r["len"],
            "cover": f"/api/v1/songs/{r['uuid']}/cover.jpg?work=1&t={r['updated']}",
            "cover_kind": ("own" if os.path.exists(os.path.join(song_dir(r["uuid"]), "cover_own.flag")) else
                           ("file" if cover_path(r["uuid"]) else "default")),
            "published": published(r),
            # a draft / a waiting version of a published song with the same notes and sound: no new mod needed
            **_meta_fields(r)}


def _meta_fields(r):
    if r["status"] == "draft":
        only, changes = meta_changes(r)
        return {"meta_only": only, "meta_changes": changes}
    if r["status"] == "pending" and r["live_hashes"] and r["hashes"] == r["live_hashes"]:
        return {"meta_only": True, "meta_changes": meta_changes(r)[1]}
    return {"meta_only": False, "meta_changes": []}


def _visible(r, words=None):
    return bool(r["live_version"]) and not r["owner_banned"] and not hidden(r, words)


@router.get("/api/v1/songs")
def song_list(q: str = "", limit: int = 50, offset: int = 0, genre: str = "", tag: str = "", age: int = 0):
    """the published songs; age: the highest age mark to show (0 = none of 16+ / 18+, 16, 18)"""
    limit, offset = max(1, min(limit, 200)), max(0, offset)
    words = ban_words()
    with _db() as con:
        rows = con.execute(SEL + " WHERE s.live_version > 0 AND s.status != 'deleted' ORDER BY s.live_at DESC").fetchall()
    ql, tag = q.strip().lower(), tag.strip().lower()
    out = []
    for r in rows:
        if not _visible(r, words):
            continue
        p = published(r, words)
        if p["age"] > age or (genre and genre not in p["genres"]) or (tag and tag not in p["tags"]):
            continue
        if ql and ql not in f"{p['title']} {p['artist']} {p['owner_nick'] or ''} {' '.join(p['tags'])}".lower():
            continue
        p.pop("chart", None)  # the list stays light; GET /api/v1/songs/{uuid} has the notes
        out.append(p)
    return {"total": len(out), "songs": out[offset:offset + limit]}


@router.get("/api/v1/genres")
def genres():
    return {"genres": genres_list()}


@router.get("/api/v1/tags")
def tag_hints(q: str = "", limit: int = 10):
    """the tags of the published (visible) songs that start with / contain q, the most used first"""
    words = ban_words()
    with _db() as con:
        rows = con.execute(SEL + " WHERE s.live_version > 0 AND s.status != 'deleted' AND s.live_tags IS NOT NULL").fetchall()
    count = {}
    for r in rows:
        if _visible(r, words):
            for t in _json(r["live_tags"], []):
                count[t] = count.get(t, 0) + 1
    ql = q.strip().lower().lstrip("#")
    hits = [(t, c) for t, c in count.items() if not ql or ql in t]
    hits.sort(key=lambda x: (not x[0].startswith(ql), -x[1], x[0]))
    return {"tags": [{"tag": t, "count": c} for t, c in hits[:max(1, min(limit, 30))]]}


@router.get("/api/v1/songs/game")
def songs_for_game():
    """for the game servers: every published song with its notes, keys, Workshop item and the SHA256 of the mod's
    files (the game checks a downloaded mod against them); the hidden ones (ban words, banned authors) are left out;
    "age" (0 / 16 / 18) — the game server shows up to its own limit"""
    words = ban_words()
    names = {g["id"]: g["ru"] + (" / " + g["en"] if g.get("en") and g["en"] != g["ru"] else "") for g in genres_list()}
    with _db() as con:
        rows = con.execute("SELECT s.*, u.nick AS owner_nick, u.banned AS owner_banned, u.trusted AS owner_trusted, u.trust AS owner_trust FROM songs s LEFT JOIN users u ON u.steamid = s.owner "
                           "WHERE s.live_version > 0 AND s.status != 'deleted' ORDER BY s.live_at DESC LIMIT 1000").fetchall()
    out = []
    for r in rows:
        if not _visible(r, words) or not r["live_chart"] or not r["live_hashes"] or not r["live_wsid"]:
            continue
        p = published(r, words)
        out.append({"uuid": p["uuid"], "title": p["title"], "artist": p["artist"], "author": p["owner"],
                    "author_nick": p["owner_nick"] if not p["owner_nick_hidden"] else barcode(p["owner"]),
                    "wsid": p["wsid"], "len": p["len"], "beat": p["beat"], "version": p["version"], "file": f"sound/mlp_g5/songs/{p['uuid']}.ogg",
                    "charts": [p["chart"]], "hashes": p["hashes"], "site": f"{SITE}/songs/{p['uuid']}" if SITE else p["page"],
                    "age": p["age"], "tags": p["tags"], "genres": [names[g] for g in p["genres"] if g in names]})
    return {"songs": out}


@router.get("/api/v1/songs/mine")
def mine(request: Request):
    user = auth.require_user(request)
    with _db() as con:
        rows = con.execute("SELECT s.*, u.nick AS owner_nick, u.banned AS owner_banned, u.trusted AS owner_trusted, u.trust AS owner_trust FROM songs s LEFT JOIN users u ON u.steamid = s.owner "
                           "WHERE s.owner = ? AND s.status != 'deleted' ORDER BY s.updated DESC", (user["steamid"],)).fetchall()
    return {"songs": [working(r) for r in rows]}


@router.get("/api/v1/songs/{u}")
def song(u: str, request: Request):
    r = _row(u)
    if _can_edit(request, r):
        return working(r)
    if not _visible(r):
        raise HTTPException(404, "no such song")
    return published(r, ban_words())


@router.get("/api/v1/songs/{u}/full.ogg")
def full_audio(u: str, request: Request):
    r = _row(u)
    path = os.path.join(song_dir(u), "full.ogg")
    if not _can_edit(request, r) or not os.path.exists(path):
        raise HTTPException(404, "no full track")
    return FileResponse(path, media_type="audio/ogg", headers={"Cache-Control": "private, no-store"})


@router.get("/api/v1/songs/{u}/preview.ogg")
def preview_audio(u: str, request: Request, work: int = 0):
    r = _row(u)
    if work:
        if not _can_edit(request, r):
            raise HTTPException(404, "no preview")
        path = os.path.join(song_dir(u), "preview.ogg")
    else:
        if not (_visible(r) or (r["live_version"] and _can_edit(request, r))):
            raise HTTPException(404, "no preview")
        path = os.path.join(song_dir(u), "preview_live.ogg")
    if not os.path.exists(path):
        raise HTTPException(404, "no preview")
    return FileResponse(path, media_type="audio/ogg")


# ---------------------------------------------------------------------------------------------- upload, editing
@router.post("/api/v1/songs")
def upload(request: Request, file: UploadFile = File(...), title: str = Form(...), artist: str = Form("")):
    user = auth.require_user(request)
    title, artist = title.strip()[:80], artist.strip()[:80]
    if not title:
        raise HTTPException(400, "a title is needed")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in EXTS:
        raise HTTPException(400, "mp3, ogg, wav, flac, m4a or opus")
    now = _now()
    with _db() as con:
        drafts = con.execute("SELECT COUNT(*) AS n FROM songs WHERE owner = ? AND status = 'draft' AND COALESCE( live_version, 0 ) = 0",
                             (user["steamid"],)).fetchone()["n"]
        recent = con.execute("SELECT COUNT(*) AS n FROM songs WHERE owner = ? AND created > ?", (user["steamid"], now - 3600)).fetchone()["n"]
    if drafts >= MAX_DRAFTS:
        raise HTTPException(429, f"at most {MAX_DRAFTS} drafts: finish or delete one")
    if recent >= UPLOADS_PER_HOUR:
        raise HTTPException(429, "too many uploads, try again later")
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in" + ext)
        size, limit = 0, int(MAX_MB * 1024 * 1024)
        with open(src, "wb") as f:
            while True:
                chunk = file.file.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"the file is over {MAX_MB:g} MB")
                f.write(chunk)
        length = _ffprobe_len(src)
        if length is None:
            raise HTTPException(400, "this doesn't look like a sound file")
        if length < MIN_SEC or length > MAX_SEC:
            raise HTTPException(400, f"the song must be {MIN_SEC}-{MAX_SEC:g} seconds long")
        u = str(uuidlib.uuid4())
        os.makedirs(song_dir(u), exist_ok=True)
        try:
            _ffmpeg(["-i", src, "-vn", "-map_metadata", "-1", "-ac", "2", "-ar", "44100", "-c:a", "libvorbis", "-q:a", "4",
                     os.path.join(song_dir(u), "source.ogg")])
            shutil.copyfile(os.path.join(song_dir(u), "source.ogg"), os.path.join(song_dir(u), "full.ogg"))
            # the picture inside the file, if any: the song's cover until the author picks another
            emb = os.path.join(song_dir(u), "cover_embedded.jpg")
            if to_cover(src, emb, "0:v:0"):
                shutil.copyfile(emb, os.path.join(song_dir(u), "cover.jpg"))
        except Exception:
            shutil.rmtree(song_dir(u), ignore_errors=True)
            raise HTTPException(400, "the file couldn't be converted")
    with _db() as con:
        con.execute("INSERT INTO songs ( uuid, owner, title, artist, status, created, updated, len, src_len, trim_start, analysis, version, live_version ) "
                    "VALUES ( ?, ?, ?, ?, 'draft', ?, ?, ?, ?, 0, 'queued', 0, 0 )", (u, user["steamid"], title, artist, now, now, round(length, 2), round(length, 2)))
    start_analysis(u, notes=False)
    return {"uuid": u}


def check_chart(times, large, length, keys=None, allow_empty=False, lens=None):
    """sorted notes + their holds + their keys ("" = random) + the holds' lengths (0 for a tap), or HTTPException
    with what's wrong (a draft may be saved with no notes; a mod needs at least one)"""
    if allow_empty and not times:
        return [], set(), [], []
    if not times or len(times) > 3000:
        raise HTTPException(400, "1-3000 notes")
    keys = list(keys or [])
    if keys and len(keys) != len(times):
        raise HTTPException(400, "one key per note")
    lens = [round(float(x or 0), 3) for x in (lens or [])]
    if lens and len(lens) != len(times):
        raise HTTPException(400, "one length per note")
    if any(k not in KEYS for k in keys):
        raise HTTPException(400, "keys: up, down, left, right, act or empty (random)")
    order = sorted(range(len(times)), key=lambda i: times[i])
    large = {order.index(i - 1) + 1 for i in large if 1 <= i <= len(times)}  # the holds follow their notes when sorted
    keys = [keys[i] for i in order] if keys else []
    lens = [lens[i] for i in order] if lens else []
    times = [times[i] for i in order]
    lens = [(min(HOLD_MAX, max(HOLD_MIN, hold_len(lens, i))) if (i + 1) in large else 0) for i in range(len(times))]
    for i, t in enumerate(times):
        if t < 0 or t > length:
            raise HTTPException(400, f"note {i + 1} is outside the song")
        if i and t - times[i - 1] < MIN_NOTE_GAP:
            raise HTTPException(400, f"notes {i} and {i + 1} are closer than {MIN_NOTE_GAP} s")
        if (i + 1) in large and i + 1 < len(times) and times[i + 1] - t < lens[i] + 0.05:
            raise HTTPException(400, f"note {i + 2} starts inside the hold {i + 1}")
        if (i + 1) in large and t + lens[i] > length + 0.01:
            raise HTTPException(400, f"the hold {i + 1} goes past the song's end")
    keys = [("" if (i + 1) in large else k) for i, k in enumerate(keys)]  # a hold takes any key
    return times, large, (keys if any(keys) else []), (lens if large else [])


def _author_draft(request, r):
    if not _can_edit(request, r) or r["status"] != "draft":
        raise HTTPException(403, "only the author's draft")


@router.put("/api/v1/songs/{u}/chart")
async def save_chart(u: str, request: Request):
    """the editor's save: { t: [ s… ], large: [ i… ] (1-based), preview_start, preview_len, title?, artist? }"""
    r = _row(u)
    _author_draft(request, r)
    try:
        body = await request.json()
        times = [round(float(x), 3) for x in body.get("t", [])]
        large = {int(i) for i in body.get("large", [])}
        keys = [str(k or "") for k in body.get("keys", [])]
        lens = [float(x or 0) for x in body.get("lens", [])]
    except Exception:
        raise HTTPException(400, "bad chart")
    length = r["len"] or 0
    times, large, keys, lens = check_chart(times, large, length, keys, allow_empty=True, lens=lens)
    ps = round(float(body.get("preview_start", r["preview_start"] or 0)), 2)
    pl = round(float(body.get("preview_len", r["preview_len"] or PREVIEW_LEN)), 2)
    if not 10 <= pl <= 30 or ps < 0 or ps + pl > length + 0.01:
        raise HTTPException(400, "the preview must be 10-30 s inside the song")
    title = str(body.get("title", r["title"]))[:80].strip() or r["title"]
    artist = str(body.get("artist", r["artist"] or ""))[:80].strip()
    tags = clean_tags(body["tags"]) if "tags" in body else _json(r["tags"], [])
    genres = clean_genres(body["genres"]) if "genres" in body else _json(r["genres"], [])
    source = clean_source(body["source"]) if "source" in body else (r["source"] or "")
    swear = max(0, min(2, int(body.get("swear", r["swear"] or 0) or 0)))
    lewd = 1 if body.get("lewd", r["lewd"]) else 0
    chart = {"t": times, "large": sorted(large)}
    if keys:
        chart["keys"] = keys
    if lens:
        chart["lens"] = lens
    if (ps, pl) != (r["preview_start"], r["preview_len"]) and os.path.exists(os.path.join(song_dir(u), "full.ogg")):
        make_preview(u, ps, pl)
    with _db() as con:
        con.execute("UPDATE songs SET chart = ?, preview_start = ?, preview_len = ?, title = ?, artist = ?, tags = ?, genres = ?, source = ?, "
                    "swear = ?, lewd = ?, updated = ? WHERE uuid = ?",
                    (json.dumps(chart), ps, pl, title, artist, json.dumps(tags, ensure_ascii=False), json.dumps(genres), source, swear, lewd, _now(), u))
        # a higher age mark is at once on the published copy too (a lower one waits for the review)
        con.execute("UPDATE songs SET live_swear = MAX( COALESCE( live_swear, 0 ), ? ), live_lewd = MAX( COALESCE( live_lewd, 0 ), ? ) "
                    "WHERE uuid = ? AND live_version > 0", (swear, lewd, u))
    return {"ok": True, "chart": chart, "tags": tags, "genres": genres, "source": source}


@router.put("/api/v1/songs/{u}/genres")
async def save_genres(u: str, request: Request):
    """the genres alone: no review, any status (the author / admins)"""
    r = _row(u)
    if not _can_edit(request, r):
        raise HTTPException(403, "only the author")
    body = await request.json()
    genres = clean_genres(body.get("genres"))
    with _db() as con:
        con.execute("UPDATE songs SET genres = ? WHERE uuid = ?", (json.dumps(genres), u))
    return {"ok": True, "genres": genres}


@router.post("/api/v1/songs/{u}/trim")
async def trim(u: str, request: Request):
    """{ start, end }: seconds of the uploaded song (the source) to keep; the notes and the beat grid move with it,
    notes outside go. { start: 0, end: null } = the whole song again."""
    r = _row(u)
    _author_draft(request, r)
    src = os.path.join(song_dir(u), "source.ogg")
    if not os.path.exists(src):
        raise HTTPException(409, "this song has no stored source (uploaded before trimming existed)")
    body = await request.json()
    src_len = r["src_len"] or r["len"] or 0
    try:
        start = max(0.0, round(float(body.get("start") or 0), 2))
        end = body.get("end")
        end = src_len if end in (None, "") else min(src_len, round(float(end), 2))
    except (TypeError, ValueError):
        raise HTTPException(400, "bad start / end")
    if end - start < MIN_SEC:
        raise HTTPException(400, f"at least {MIN_SEC} seconds must stay")
    whole = start <= 0.01 and end >= src_len - 0.01
    full = os.path.join(song_dir(u), "full.ogg")
    if whole:
        shutil.copyfile(src, full)
    else:
        length = end - start
        _ffmpeg(["-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", src, "-af",
                 f"afade=t=in:d={TRIM_FADE},afade=t=out:st={max(0, length - TRIM_FADE):.2f}:d={TRIM_FADE}",
                 "-map_metadata", "-1", "-c:a", "libvorbis", "-q:a", "4", full])
    new_len = round(_ffprobe_len(full) or (end - start), 2)
    shift = (r["trim_start"] or 0) - start          # old song time -> new song time
    chart = json.loads(r["chart"]) if r["chart"] else {"t": [], "large": []}
    L, keys, lens = set(chart.get("large", [])), chart.get("keys") or [], chart.get("lens") or []
    nt, nl, nk, nn = [], [], [], []
    for i, t in enumerate(chart.get("t", [])):
        t2 = round(t + shift, 3)
        if 0 <= t2 <= new_len - (hold_len(lens, i) if (i + 1) in L else 0):
            nt.append(t2)
            if (i + 1) in L:
                nl.append(len(nt))
            nk.append(keys[i] if i < len(keys) else "")
            nn.append(hold_len(lens, i) if (i + 1) in L else 0)
    # the beat grid: the known beats moved, the rest of the new piece filled in by the song's tempo
    # (a piece made longer again would have no grid where beats were cut off before)
    old_beats = [b + shift for b in (json.loads(r["beats"]) if r["beats"] else [])]
    step = r["beat"] or 0
    beats = [b for b in old_beats if 0 <= b <= new_len]
    if old_beats and step > 0.1:
        t = min(old_beats) - step
        while t >= 0:
            beats.append(t)
            t -= step
        t = max(old_beats) + step
        while t <= new_len:
            beats.append(t)
            t += step
    beats = sorted(round(b, 3) for b in beats)
    chart = {"t": nt, "large": nl}
    if any(nk):
        chart["keys"] = nk
    if nl:
        chart["lens"] = nn
    ps = min(max(0.0, (r["preview_start"] or 0) + shift), max(0.0, new_len - (r["preview_len"] or PREVIEW_LEN)))
    pl = min(r["preview_len"] or PREVIEW_LEN, new_len)
    make_preview(u, ps, pl)
    with _db() as con:
        con.execute("UPDATE songs SET len = ?, trim_start = ?, trim_end = ?, chart = ?, beats = ?, preview_start = ?, preview_len = ?, "
                    "updated = ? WHERE uuid = ?", (new_len, 0 if whole else start, None if whole else end, json.dumps(chart),
                                                   json.dumps(beats), round(ps, 2), round(pl, 2), _now(), u))
    return {"ok": True, "len": new_len, "notes": len(nt)}


@router.get("/api/v1/songs/{u}/cover.jpg")
def cover(u: str, request: Request, work: int = 0):
    r = _row(u)
    if work:
        if not _can_edit(request, r):
            raise HTTPException(404, "no cover")
        path = cover_path(u)
    else:
        if not (_visible(r) or (r["live_version"] and _can_edit(request, r))):
            raise HTTPException(404, "no cover")
        path = cover_path(u, live=True)
    return FileResponse(path or DEFAULT_COVER, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600" if not work else "private, no-store"})


@router.post("/api/v1/songs/{u}/cover")
def set_cover(u: str, request: Request, file: UploadFile = File(...)):
    r = _row(u)
    _author_draft(request, r)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in COVER_EXTS:
        raise HTTPException(400, "jpg, png or webp")
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in" + ext)
        data = file.file.read(int(COVER_MAX_MB * 1024 * 1024) + 1)
        if len(data) > COVER_MAX_MB * 1024 * 1024:
            raise HTTPException(413, f"the picture is over {COVER_MAX_MB} MB")
        with open(src, "wb") as f:
            f.write(data)
        if not to_cover(src, os.path.join(song_dir(u), "cover.jpg")):
            raise HTTPException(400, "this doesn't look like a picture")
    open(os.path.join(song_dir(u), "cover_own.flag"), "w").close()
    with _db() as con:
        con.execute("UPDATE songs SET updated = ? WHERE uuid = ?", (_now(), u))
    return {"ok": True}


@router.delete("/api/v1/songs/{u}/cover")
def drop_cover(u: str, request: Request):
    """the author's own cover goes: back to the picture in the audio file, or none (the default)"""
    r = _row(u)
    _author_draft(request, r)
    d = song_dir(u)
    for name in ("cover.jpg", "cover_own.flag"):
        if os.path.exists(os.path.join(d, name)):
            os.remove(os.path.join(d, name))
    if os.path.exists(os.path.join(d, "cover_embedded.jpg")):
        shutil.copyfile(os.path.join(d, "cover_embedded.jpg"), os.path.join(d, "cover.jpg"))
    with _db() as con:
        con.execute("UPDATE songs SET updated = ? WHERE uuid = ?", (_now(), u))
    return {"ok": True}


@router.post("/api/v1/songs/{u}/auto")
def redo_auto(u: str, request: Request):
    r = _row(u)
    _author_draft(request, r)
    if not os.path.exists(os.path.join(song_dir(u), "full.ogg")):
        raise HTTPException(409, "no full track")
    with _db() as con:
        con.execute("UPDATE songs SET analysis = 'queued', updated = ? WHERE uuid = ?", (_now(), u))
    start_analysis(u)
    return {"ok": True}


@router.post("/api/v1/songs/{u}/revise")
def revise(u: str, request: Request):
    """an approved (or rejected) song back to the editor: a new version; the published copy stays as it is"""
    r = _row(u)
    if not _can_edit(request, r) or r["status"] not in ("approved", "rejected"):
        raise HTTPException(403, "only an approved / rejected song of the author")
    with _db() as con:
        con.execute("UPDATE songs SET status = 'draft', hashes = NULL, reason = NULL, updated = ? WHERE uuid = ?", (_now(), u))
    return {"ok": True}


@router.delete("/api/v1/songs/{u}")
def delete(u: str, request: Request):
    r = _row(u)
    user = auth.require_user(request)
    own_unpublished = user["steamid"] == r["owner"] and not r["live_version"] and r["status"] in ("draft", "built", "rejected")
    if not (user["admin"] or own_unpublished):
        raise HTTPException(403, "only an unpublished song of the author (or an admin)")
    with _db() as con:
        con.execute("UPDATE songs SET status = 'deleted', updated = ? WHERE uuid = ?", (_now(), u))
    shutil.rmtree(song_dir(u), ignore_errors=True)  # an analysis still running finishes the job (see _analyse)
    return {"ok": True}


# ---------------------------------------------------------------------------------------------- the song mod
def mod_files(r):
    """{ path in the addon: bytes } of the working copy's song mod (the data file is read as text by the mod, never run)"""
    u = r["uuid"]
    chart = json.loads(r["chart"])
    data = {"format": 1, "uuid": u, "title": r["title"], "artist": r["artist"] or "", "author": r["owner"],
            "file": f"sound/mlp_g5/songs/{u}.ogg", "len": r["len"], "beat": r["beat"] or 0.5,
            "charts": [{"t": chart["t"], "large": chart["large"], **({"keys": chart["keys"]} if chart.get("keys") else {}),
                        **({"lens": chart["lens"]} if chart.get("lens") else {})}]}
    if SITE:
        data["site"] = f"{SITE}/songs/{u}"
    js = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    eq = "=="
    while f"]{eq}]" in js:  # a long-string level the text doesn't contain
        eq += "="
    lua = ("-- My Little Pony: Maretime Bay — a song for the dance minigame. DATA ONLY: the mod reads this file as\n"
           "-- text and never runs it. Made with the song editor" + (f" ({SITE})" if SITE else "") + ".\n"
           f"return [{eq}[{js}]{eq}]\n")
    with open(os.path.join(song_dir(u), "full.ogg"), "rb") as f:
        ogg = f.read()
    return {f"lua/mlp_g5_songs/{u}.lua": lua.encode("utf-8"), f"sound/mlp_g5/songs/{u}.ogg": ogg}


def addon_json(r):
    return json.dumps({"title": f"MLP Dance song: {r['title']}"[:120], "type": "servercontent", "tags": ["fun", "cartoon"],
                       "ignore": ["README.txt", "addon.jpg"]}, ensure_ascii=False, indent=2)


README = """Песня для мини-игры «Танец» мода My Little Pony: Maretime Bay (Garry's Mod)

1. Проверить у себя: распакуйте папку {folder} в garrysmod/addons/ и запустите режим — песня появится
   в голосовании в лобби танца.
2. Выложить в Мастерскую. Проще всего — программой gmpublisher (https://github.com/WilliamVenner/gmpublisher):
   выберите папку {folder}, значок addon.jpg она возьмёт сама. Или командами (папка garrysmod/bin):
     gmad.exe create -folder "<путь>/{folder}" -out "<путь>/{folder}.gma"
     gmpublish.exe create -addon "<путь>/{folder}.gma" -icon "<путь>/{folder}/addon.jpg"
   Новая версия той же песни — обновите уже выложенный мод:
     gmpublish.exe update -addon "<путь>/{folder}.gma" -id <номер мода>
   (или любым инструментом публикации аддонов GMod). Название и описание можно поменять в Мастерской.
3. Вставьте ссылку на мод на странице редактора песни и отправьте на проверку.
   Не меняйте файлы мода: игра сверяет их с сайтом и не подключит изменённую песню.

A song for the «Dance» minigame of the My Little Pony: Maretime Bay mod (Garry's Mod)

1. Test it yourself: unpack the {folder} folder into garrysmod/addons/ and start the gamemode — the song appears
   in the dance lobby's vote.
2. Publish it to the Workshop. The easiest way is gmpublisher (https://github.com/WilliamVenner/gmpublisher):
   pick the {folder} folder, it takes the addon.jpg icon by itself. Or with the commands (the garrysmod/bin folder):
     gmad.exe create -folder "<path>/{folder}" -out "<path>/{folder}.gma"
     gmpublish.exe create -addon "<path>/{folder}.gma" -icon "<path>/{folder}/addon.jpg"
   A new version of the same song — update the mod you already published:
     gmpublish.exe update -addon "<path>/{folder}.gma" -id <mod number>
   (or with any GMod addon publishing tool). The title and the description can be changed on the Workshop.
3. Paste the mod's link on the song editor page and send it for review.
   Don't change the mod's files: the game checks them against the site and won't mount a changed song.
"""


@router.post("/api/v1/songs/{u}/build")
def build(u: str, request: Request):
    r = _row(u)
    _author_draft(request, r)
    if r["analysis"] != "done" or not r["chart"] or not os.path.exists(os.path.join(song_dir(u), "full.ogg")):
        raise HTTPException(409, "the song isn't ready")
    chart = json.loads(r["chart"])
    if not chart.get("t"):
        raise HTTPException(400, "no notes yet: place them (or ask for the automatic ones) and save")
    check_chart(chart["t"], set(chart["large"]), r["len"] or 0, chart.get("keys"), lens=chart.get("lens"))
    files = mod_files(r)
    hashes = {path: hashlib.sha256(data).hexdigest() for path, data in files.items()}
    with _db() as con:
        con.execute("UPDATE songs SET status = 'built', hashes = ?, updated = ? WHERE uuid = ?", (json.dumps(hashes), _now(), u))
    return {"ok": True, "hashes": hashes}


@router.get("/api/v1/songs/{u}/mod.zip")
def mod_zip(u: str, request: Request):
    r = _row(u)
    if not _can_edit(request, r) or r["status"] not in ("built", "pending", "approved"):
        raise HTTPException(404, "build the mod first")
    folder = "mlp_song_" + u.split("-")[0]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, data in mod_files(r).items():
            z.writestr(f"{folder}/{path}", data, compress_type=zipfile.ZIP_STORED if path.endswith(".ogg") else zipfile.ZIP_DEFLATED)
        z.writestr(f"{folder}/addon.json", addon_json(r))
        z.writestr(f"{folder}/README.txt", README.format(folder=folder))
        z.writestr(f"{folder}/addon.jpg", addon_icon(cover_path(u) or DEFAULT_COVER))  # the Workshop icon: the song's cover
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{folder}.zip"', "Cache-Control": "private, no-store"})


@router.post("/api/v1/songs/{u}/unbuild")
def unbuild(u: str, request: Request):
    r = _row(u)
    if not _can_edit(request, r) or r["status"] != "built":
        raise HTTPException(403, "only a built song of the author")
    with _db() as con:
        con.execute("UPDATE songs SET status = 'draft', hashes = NULL, updated = ? WHERE uuid = ?", (_now(), u))
    return {"ok": True}


WSID_RE = re.compile(r"(?:[?&]id=|^)(\d{6,20})(?:\D|$)")


def workshop_item(wsid):
    """Steam's facts about a Workshop item (no key needed), or None"""
    body = urllib.parse.urlencode({"itemcount": 1, "publishedfileids[0]": wsid}).encode()
    req = urllib.request.Request("https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/", data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "mlp-g5-web"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            d = json.loads(resp.read(200000))["response"]["publishedfiledetails"][0]
    except Exception:
        return None
    return d if d.get("result") == 1 else None


@router.post("/api/v1/songs/{u}/submit")
async def submit(u: str, request: Request):
    r = _row(u)
    user = auth.require_user(request)
    meta = False
    if user["steamid"] == r["owner"] and r["status"] == "draft":
        # only the title / artist / cover / preview changed: the published mod (the same Workshop item) stays
        meta, changes = meta_changes(r)
        if meta and not changes:
            raise HTTPException(409, "nothing changed since the published version")
    if user["steamid"] != r["owner"] or (r["status"] != "built" and not meta):
        raise HTTPException(403, "build the mod first (only the author)")
    body = await request.json() if not meta else {"workshop": r["live_wsid"]}
    m = WSID_RE.search(str(body.get("workshop", "") or r["wsid"] or "").strip())
    if not m:
        raise HTTPException(400, "a Workshop link (…/filedetails/?id=123…) or its number")
    wsid = m.group(1)
    with _db() as con:
        taken = con.execute("SELECT uuid FROM songs WHERE ( wsid = ? OR live_wsid = ? ) AND uuid != ? AND status != 'deleted'", (wsid, wsid, u)).fetchone()
    if taken:
        raise HTTPException(409, "this Workshop item is already another song")
    d = workshop_item(wsid)
    if not d:
        raise HTTPException(404, "Steam doesn't know this Workshop item (or it's hidden)")
    if int(d.get("consumer_app_id") or 0) != 4000:
        raise HTTPException(400, "this isn't a Garry's Mod item")
    if str(d.get("creator")) != user["steamid"]:
        raise HTTPException(403, "this item was published by another Steam account")
    if int(d.get("banned") or 0):
        raise HTTPException(400, "Steam has banned this item")
    ws_title, ws_desc = str(d.get("title") or "")[:300], str(d.get("description") or "")[:8000]
    if filter_hits(song_fields(r, False, {"title": ws_title, "description": ws_desc})):
        with _db() as con:
            con.execute("UPDATE songs SET ws_title = ?, ws_desc = ? WHERE uuid = ?", (ws_title, ws_desc, u))
            if (r["filter_at"] or 0) < _now() - 86400:   # a penalty once a day per song (trying again and again isn't worse)
                con.execute("UPDATE songs SET filter_at = ? WHERE uuid = ?", (_now(), u))
                add_trust(con, r["owner"], TRUST_FILTER)
        raise HTTPException(422, "filtered")
    with _db() as con:
        if meta:
            con.execute("UPDATE songs SET status = 'pending', wsid = ?, hashes = live_hashes, ws_title = ?, ws_desc = ?, updated = ? WHERE uuid = ?",
                        (wsid, ws_title, ws_desc, _now(), u))
        else:
            con.execute("UPDATE songs SET status = 'pending', wsid = ?, ws_title = ?, ws_desc = ?, updated = ? WHERE uuid = ?",
                        (wsid, ws_title, ws_desc, _now(), u))
    # a trusted author's new version of a published song: approved at once (no admin, no trust points for it)
    auto = bool(r["live_version"] and r["owner_trusted"])
    if auto:
        approve(u, "auto")
    return {"ok": True, "wsid": wsid, "workshop_title": d.get("title"), "meta_only": meta, "auto_approved": auto}


# ---------------------------------------------------------------------------------------------- admin
def _admin(request):
    if not auth.is_admin(request):
        raise HTTPException(403, "admin only")


@router.get("/api/v1/admin/songs")
def admin_songs(request: Request, status: str = "", q: str = "", limit: int = 100):
    _admin(request)
    words = ban_words()
    sql, args = "SELECT s.*, u.nick AS owner_nick, u.banned AS owner_banned, u.trusted AS owner_trusted, u.trust AS owner_trust FROM songs s LEFT JOIN users u ON u.steamid = s.owner WHERE s.status != 'deleted'", []
    if status:
        sql += " AND s.status = ?"
        args.append(status)
    sql += " ORDER BY CASE s.status WHEN 'pending' THEN 0 ELSE 1 END, s.updated DESC"
    with _db() as con:
        rows = con.execute(sql, args).fetchall()
    ql = q.strip().lower()
    out = []
    for r in rows:
        hay = f"{r['title']} {r['artist'] or ''} {r['live_title'] or ''} {r['owner_nick'] or ''} {r['owner']} {r['uuid']} {r['wsid'] or ''}".lower()
        if ql and ql not in hay:
            continue
        w = working(r)
        w.pop("beats", None)
        # every hit of the filter: the working copy, the published one, the author's nick
        hits = filter_hits(song_fields(r, False), words)
        if r["live_version"]:
            hits += [dict(h, live=True) for h in filter_hits(song_fields(r, True), words) if h not in hits]
        hits += filter_hits([("nick", r["owner_nick"])], words)
        w["filter_hits"] = hits
        w["hidden"] = hidden(r, words)
        w["owner_banned"] = bool(r["owner_banned"])
        w["owner_trust"] = r["owner_trust"] or 0
        out.append(w)
        if len(out) >= max(1, min(limit, 500)):
            break
    return {"songs": out}


@router.post("/api/v1/admin/songs/{u}/approve")
def admin_approve(u: str, request: Request):
    """the working copy becomes the published one (the game servers switch to it); the author gets a trust point"""
    _admin(request)
    return approve(u, auth.current_user(request)["steamid"])


def approve(u, by):
    r = _row(u)
    if r["status"] != "pending" or not r["hashes"] or not r["wsid"]:
        raise HTTPException(409, "only a song waiting for the audit")
    pv = os.path.join(song_dir(u), "preview.ogg")
    if os.path.exists(pv):
        shutil.copyfile(pv, os.path.join(song_dir(u), "preview_live.ogg"))
    cv, cl = cover_path(u), os.path.join(song_dir(u), "cover_live.jpg")
    if cv:
        shutil.copyfile(cv, cl)
    elif os.path.exists(cl):
        os.remove(cl)
    ver = (r["live_version"] or 0) + 1
    with _db() as con:
        con.execute("UPDATE songs SET status = 'approved', reason = NULL, version = ?, live_version = ?, live_at = ?, live_title = title, "
                    "live_artist = artist, live_chart = chart, live_hashes = hashes, live_wsid = wsid, live_len = len, live_beat = beat, "
                    "live_tags = tags, live_source = source, live_swear = swear, live_lewd = lewd, approved_by = ?, "
                    "updated = ? WHERE uuid = ?", (ver, ver, _now(), by, _now(), u))
        if by != "auto":
            add_trust(con, r["owner"], TRUST_APPROVE)
    return {"ok": True, "version": ver}


@router.post("/api/v1/admin/songs/{u}/reject")
async def admin_reject(u: str, request: Request):
    _admin(request)
    r = _row(u)
    if r["status"] != "pending":
        raise HTTPException(409, "only a song waiting for the audit")
    body = await request.json()
    reason = str(body.get("reason", "")).strip()[:300]
    with _db() as con:
        con.execute("UPDATE songs SET status = 'rejected', reason = ?, updated = ? WHERE uuid = ?", (reason or None, _now(), u))
        add_trust(con, r["owner"], TRUST_REJECT)
    return {"ok": True}


@router.get("/api/v1/admin/banwords")
def admin_banwords(request: Request):
    _admin(request)
    return {"words": ban_words()}


@router.put("/api/v1/admin/banwords")
async def admin_set_banwords(request: Request):
    _admin(request)
    body = await request.json()
    words, seen = [], set()
    for w in body.get("words", []):
        w = str(w).strip()[:60]
        if w and w.lower() not in seen:
            seen.add(w.lower())
            words.append(w)
    with _db() as con:
        con.execute("REPLACE INTO meta ( key, value ) VALUES ( 'ban_words', ? )", (json.dumps(words[:2000], ensure_ascii=False),))
    _WORDS["t"] = 0
    return {"words": words[:2000]}


@router.get("/api/v1/admin/genres")
def admin_genres(request: Request):
    _admin(request)
    return {"genres": genres_list()}


@router.put("/api/v1/admin/genres")
async def admin_set_genres(request: Request):
    """{ genres: [ { id?, ru, en } ] } (or lines "Русский | English"): a genre found by its id or either name keeps
    its id (the songs keep it); a new one gets a new id; one left out disappears from the songs"""
    _admin(request)
    body = await request.json()
    old = genres_list()
    by_name = {}
    for g in old:
        for k in ("ru", "en"):
            if g.get(k):
                by_name.setdefault(g[k].strip().lower(), g["id"])
    ids_old = {g["id"] for g in old}
    out, used = [], set()
    for g in body.get("genres", [])[:100]:
        if isinstance(g, str):
            parts = [x.strip() for x in g.split("|")]
            g = {"ru": parts[0], "en": parts[1] if len(parts) > 1 else ""}
        ru, en = str(g.get("ru", "")).strip()[:40], str(g.get("en", "")).strip()[:40]
        if not ru:
            continue
        gid = g.get("id") if g.get("id") in ids_old else (by_name.get(ru.lower()) or by_name.get(en.lower()))
        if not gid or gid in used:
            base = re.sub(r"[^a-z0-9]+", "", (en or ru).lower())[:16] or "g"
            gid, k = base, 2
            while gid in used or gid in ids_old:
                gid, k = f"{base}{k}", k + 1
        used.add(gid)
        out.append({"id": gid, "ru": ru, "en": en or ru})
    with _db() as con:
        con.execute("REPLACE INTO meta ( key, value ) VALUES ( 'genres', ? )", (json.dumps(out, ensure_ascii=False),))
    return {"genres": out}


@router.post("/api/v1/admin/users/{steamid}/trust")
async def admin_trust(steamid: str, request: Request):
    """{ trusted }: give / take the trusted mark by hand (the points are set to match)"""
    _admin(request)
    if not re.fullmatch(r"\d{17}", steamid):
        raise HTTPException(400, "bad steamid")
    body = await request.json()
    on = bool(body.get("trusted"))
    with _db() as con:
        con.execute("UPDATE users SET trusted = ?, trust = ? WHERE steamid = ?", (1 if on else 0, TRUST_AT if on else 0, steamid))
    return {"ok": True, "trusted": on}


@router.post("/api/v1/admin/users/{steamid}/ban")
async def admin_ban(steamid: str, request: Request):
    """a banned author can't log in to upload / edit, and their songs leave the public list"""
    _admin(request)
    if not re.fullmatch(r"\d{17}", steamid):
        raise HTTPException(400, "bad steamid")
    body = await request.json()
    banned = 1 if body.get("banned", True) else 0
    with _db() as con:
        con.execute("INSERT INTO users ( steamid, nick, banned, ban_reason ) VALUES ( ?, ?, ?, ? ) "
                    "ON CONFLICT ( steamid ) DO UPDATE SET banned = excluded.banned, ban_reason = excluded.ban_reason",
                    (steamid, steamid, banned, str(body.get("reason", ""))[:200] or None))
    return {"ok": True, "banned": bool(banned)}
