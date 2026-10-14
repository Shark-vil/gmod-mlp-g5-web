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
Titles / artists with a word of the admins' BAN_WORDS list are hidden from the public list (not deleted).

  POST   /api/v1/songs                  multipart: file, title, artist   -> { uuid }
  GET    /api/v1/songs                  the published songs (?q= search, ?limit, ?offset)
  GET    /api/v1/songs/mine             the player's own songs
  GET    /api/v1/songs/{uuid}           the song: published copy for everybody, working copy + beat grid for the author / admins
  GET    /api/v1/songs/{uuid}/full.ogg  the full track (author / admins)
  GET    /api/v1/songs/{uuid}/preview.ogg   the published fragment (?work=1: the working one, author / admins)
  PUT    /api/v1/songs/{uuid}/chart     the editor's save (author, draft): notes, holds, keys, preview fragment, title
  POST   /api/v1/songs/{uuid}/trim      { start, end } cut the song out of the uploaded source (author, draft); the notes move with it
  GET    /api/v1/songs/{uuid}/cover.jpg the published cover (?work=1: the working one), or the default picture
  POST   /api/v1/songs/{uuid}/cover     multipart image: the author's own cover (512x512 jpg); DELETE: back to the file's own / none
  POST   /api/v1/songs/{uuid}/auto      redo the automatic chart (author, draft)
  POST   /api/v1/songs/{uuid}/build     the working copy -> the song mod: status "built", SHA256 of its files kept
  GET    /api/v1/songs/{uuid}/mod.zip   the song mod (author / admins)
  POST   /api/v1/songs/{uuid}/unbuild   built -> draft
  POST   /api/v1/songs/{uuid}/submit    { workshop } -> checked with Steam -> pending
  POST   /api/v1/songs/{uuid}/revise    approved / rejected -> draft (a new version; the published one stays)
  DELETE /api/v1/songs/{uuid}           the author (never published) or an admin
  admin: GET /api/v1/admin/songs (?status, ?q), POST …/{uuid}/approve, POST …/{uuid}/reject { reason },
         GET / PUT /api/v1/admin/banwords { words }, POST /api/v1/admin/users/{steamid}/ban { banned, reason }
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
HOLD_LEN = 1.94      # a hold lasts 4 of the game's beats (sh_mg_dance.lua D.HOLD)
MIN_NOTE_GAP = 0.25  # closer, one press would count for two notes (the GOOD window is ±0.2 s)

router = APIRouter()
_db = None
_dir = None
_work = threading.Semaphore(1)  # one analysis at a time (librosa is heavy)

KEYS = ("", "up", "down", "left", "right", "act")  # a note's key: "" = random (a new one every game), like the game's own
TRIM_FADE = 0.4  # seconds of fade at a cut edge (no click)
COVER_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
COVER_MAX_MB = 5
DEFAULT_COVER = os.path.join(os.path.dirname(__file__), "static", "song_icon.jpg")
LIVE_COLS = {"trim_start": "REAL DEFAULT 0", "trim_end": "REAL", "src_len": "REAL", "live_title": "TEXT", "live_artist": "TEXT", "live_chart": "TEXT", "live_hashes": "TEXT", "live_wsid": "TEXT",
             "live_len": "REAL", "live_beat": "REAL", "live_version": "INTEGER DEFAULT 0", "live_at": "INTEGER", "version": "INTEGER DEFAULT 0"}


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


def _analyse(u):
    with _work:
        with _db() as con:
            con.execute("UPDATE songs SET analysis = 'running', updated = ? WHERE uuid = ?", (_now(), u))
        try:
            res = chart_auto.analyze(os.path.join(song_dir(u), "full.ogg"))
            start = round(max(0.0, min(res["len"] - PREVIEW_LEN, res["len"] * 0.3)), 2)
            length = round(min(PREVIEW_LEN, res["len"]), 2)
            with _db() as con:
                r = con.execute("SELECT preview_start, preview_len FROM songs WHERE uuid = ?", (u,)).fetchone()
            make_preview(u, r["preview_start"] if r and r["preview_start"] is not None else start,
                         r["preview_len"] if r and r["preview_len"] else length)
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


def start_analysis(u):
    threading.Thread(target=_analyse, args=(u,), daemon=True).start()


# ---------------------------------------------------------------------------------------------- ban words
def ban_words():
    with _db() as con:
        r = con.execute("SELECT value FROM meta WHERE key = 'ban_words'").fetchone()
    try:
        return json.loads(r["value"]) if r else []
    except Exception:
        return []


def hidden(title, artist, words=None):
    text = f"{title or ''} {artist or ''}".lower()
    return any(w and w.lower() in text for w in (ban_words() if words is None else words))


# ---------------------------------------------------------------------------------------------- reading
def _row(u):
    if not UUID_RE.match(u or ""):
        raise HTTPException(404, "no such song")
    with _db() as con:
        r = con.execute("SELECT s.*, u.nick AS owner_nick, u.banned AS owner_banned FROM songs s LEFT JOIN users u ON u.steamid = s.owner "
                        "WHERE s.uuid = ?", (u,)).fetchone()
    if not r or r["status"] == "deleted":
        raise HTTPException(404, "no such song")
    return r


def _can_edit(request, r):
    u = auth.current_user(request)
    return bool(u and (u["admin"] or u["steamid"] == r["owner"]))


def _workshop(wsid):
    return f"https://steamcommunity.com/sharedfiles/filedetails/?id={wsid}" if wsid else None


def published(r):
    """the published copy (what the public and the game servers get), or None"""
    if not r["live_version"]:
        return None
    return {"uuid": r["uuid"], "title": r["live_title"], "artist": r["live_artist"] or "", "owner": r["owner"],
            "owner_nick": r["owner_nick"], "owner_profile": f"https://steamcommunity.com/profiles/{r['owner']}",
            "len": r["live_len"], "beat": r["live_beat"], "version": r["live_version"], "approved_at": r["live_at"],
            "chart": json.loads(r["live_chart"]) if r["live_chart"] else None,
            "hashes": json.loads(r["live_hashes"]) if r["live_hashes"] else None,  # the game checks the downloaded mod against these
            "wsid": r["live_wsid"], "workshop": _workshop(r["live_wsid"]),
            "preview": f"/api/v1/songs/{r['uuid']}/preview.ogg", "page": f"/songs/{r['uuid']}",
            "cover": f"/api/v1/songs/{r['uuid']}/cover.jpg?v={r['live_version']}"}


def working(r):
    """the working copy, for the author / admins"""
    return {"uuid": r["uuid"], "title": r["title"], "artist": r["artist"], "status": r["status"], "owner": r["owner"],
            "owner_nick": r["owner_nick"], "owner_profile": f"https://steamcommunity.com/profiles/{r['owner']}",
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
            "published": published(r)}


def _visible(r, words=None):
    return bool(r["live_version"]) and not r["owner_banned"] and not hidden(r["live_title"], r["live_artist"], words)


@router.get("/api/v1/songs")
def song_list(q: str = "", limit: int = 50, offset: int = 0):
    limit, offset = max(1, min(limit, 200)), max(0, offset)
    words = ban_words()
    with _db() as con:
        rows = con.execute("SELECT s.*, u.nick AS owner_nick, u.banned AS owner_banned FROM songs s LEFT JOIN users u ON u.steamid = s.owner "
                           "WHERE s.live_version > 0 AND s.status != 'deleted' ORDER BY s.live_at DESC").fetchall()
    ql = q.strip().lower()
    out = [published(r) for r in rows if _visible(r, words)
           and (not ql or ql in f"{r['live_title']} {r['live_artist'] or ''} {r['owner_nick'] or ''}".lower())]
    for s in out:
        s.pop("chart", None)  # the list stays light; GET /api/v1/songs/{uuid} has the notes
    return {"total": len(out), "songs": out[offset:offset + limit]}


@router.get("/api/v1/songs/mine")
def mine(request: Request):
    user = auth.require_user(request)
    with _db() as con:
        rows = con.execute("SELECT s.*, u.nick AS owner_nick, u.banned AS owner_banned FROM songs s LEFT JOIN users u ON u.steamid = s.owner "
                           "WHERE s.owner = ? AND s.status != 'deleted' ORDER BY s.updated DESC", (user["steamid"],)).fetchall()
    return {"songs": [working(r) for r in rows]}


@router.get("/api/v1/songs/{u}")
def song(u: str, request: Request):
    r = _row(u)
    if _can_edit(request, r):
        return working(r)
    if not _visible(r):
        raise HTTPException(404, "no such song")
    return published(r)


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
    start_analysis(u)
    return {"uuid": u}


def check_chart(times, large, length, keys=None):
    """sorted notes + their holds + their keys ("" = random), or HTTPException with what's wrong"""
    if not times or len(times) > 3000:
        raise HTTPException(400, "1-3000 notes")
    keys = list(keys or [])
    if keys and len(keys) != len(times):
        raise HTTPException(400, "one key per note")
    if any(k not in KEYS for k in keys):
        raise HTTPException(400, "keys: up, down, left, right, act or empty (random)")
    order = sorted(range(len(times)), key=lambda i: times[i])
    large = {order.index(i - 1) + 1 for i in large if 1 <= i <= len(times)}  # the holds follow their notes when sorted
    keys = [keys[i] for i in order] if keys else []
    times = [times[i] for i in order]
    for i, t in enumerate(times):
        if t < 0 or t > length:
            raise HTTPException(400, f"note {i + 1} is outside the song")
        if i and t - times[i - 1] < MIN_NOTE_GAP:
            raise HTTPException(400, f"notes {i} and {i + 1} are closer than {MIN_NOTE_GAP} s")
        if (i + 1) in large and i + 1 < len(times) and times[i + 1] - t < HOLD_LEN + 0.05:
            raise HTTPException(400, f"note {i + 2} starts inside the hold {i + 1}")
    keys = [("" if (i + 1) in large else k) for i, k in enumerate(keys)]  # a hold takes any key
    return times, large, (keys if any(keys) else [])


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
    except Exception:
        raise HTTPException(400, "bad chart")
    length = r["len"] or 0
    times, large, keys = check_chart(times, large, length, keys)
    ps = round(float(body.get("preview_start", r["preview_start"] or 0)), 2)
    pl = round(float(body.get("preview_len", r["preview_len"] or PREVIEW_LEN)), 2)
    if not 10 <= pl <= 30 or ps < 0 or ps + pl > length + 0.01:
        raise HTTPException(400, "the preview must be 10-30 s inside the song")
    title = str(body.get("title", r["title"]))[:80].strip() or r["title"]
    artist = str(body.get("artist", r["artist"] or ""))[:80].strip()
    chart = {"t": times, "large": sorted(large)}
    if keys:
        chart["keys"] = keys
    if (ps, pl) != (r["preview_start"], r["preview_len"]) and os.path.exists(os.path.join(song_dir(u), "full.ogg")):
        make_preview(u, ps, pl)
    with _db() as con:
        con.execute("UPDATE songs SET chart = ?, preview_start = ?, preview_len = ?, title = ?, artist = ?, updated = ? WHERE uuid = ?",
                    (json.dumps(chart), ps, pl, title, artist, _now(), u))
    return {"ok": True, "chart": chart}


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
    L, keys = set(chart.get("large", [])), chart.get("keys") or []
    nt, nl, nk = [], [], []
    for i, t in enumerate(chart.get("t", [])):
        t2 = round(t + shift, 3)
        if 0 <= t2 <= new_len - (HOLD_LEN if (i + 1) in L else 0):
            nt.append(t2)
            if (i + 1) in L:
                nl.append(len(nt))
            nk.append(keys[i] if i < len(keys) else "")
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
            "charts": [{"t": chart["t"], "large": chart["large"], **({"keys": chart["keys"]} if chart.get("keys") else {})}]}
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
                       "ignore": ["README.txt", "icon.jpg"]}, ensure_ascii=False, indent=2)


README = """Песня для мини-игры «Танец» мода My Little Pony: Maretime Bay (Garry's Mod)

1. Проверить у себя: распакуйте папку {folder} в garrysmod/addons/ и запустите режим — песня появится
   в голосовании в лобби танца.
2. Выложить в Мастерскую (папка garrysmod/bin):
     gmad.exe create -folder "<путь>/{folder}" -out "<путь>/{folder}.gma"
     gmpublish.exe create -addon "<путь>/{folder}.gma" -icon "<путь>/{folder}/icon.jpg"
   Новая версия той же песни — обновите уже выложенный мод:
     gmpublish.exe update -addon "<путь>/{folder}.gma" -id <номер мода>
   (или любым инструментом публикации аддонов GMod). Название и описание можно поменять в Мастерской.
3. Вставьте ссылку на мод на странице редактора песни и отправьте на проверку.
   Не меняйте файлы мода: игра сверяет их с сайтом и не подключит изменённую песню.

A song for the dance minigame of My Little Pony: Maretime Bay (Garry's Mod): test it in garrysmod/addons/,
publish it with gmad / gmpublish (garrysmod/bin; a new version: gmpublish update -id <item>), paste the Workshop
link in the song editor. Don't change the files: the game checks them against the site.
"""


@router.post("/api/v1/songs/{u}/build")
def build(u: str, request: Request):
    r = _row(u)
    _author_draft(request, r)
    if r["analysis"] != "done" or not r["chart"] or not os.path.exists(os.path.join(song_dir(u), "full.ogg")):
        raise HTTPException(409, "the song isn't ready")
    chart = json.loads(r["chart"])
    check_chart(chart["t"], set(chart["large"]), r["len"] or 0, chart.get("keys"))
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
        with open(cover_path(u) or DEFAULT_COVER, "rb") as f:  # the Workshop icon: the song's cover
            z.writestr(f"{folder}/icon.jpg", f.read())
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
    if user["steamid"] != r["owner"] or r["status"] != "built":
        raise HTTPException(403, "build the mod first (only the author)")
    body = await request.json()
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
    with _db() as con:
        con.execute("UPDATE songs SET status = 'pending', wsid = ?, updated = ? WHERE uuid = ?", (wsid, _now(), u))
    return {"ok": True, "wsid": wsid, "workshop_title": d.get("title")}


# ---------------------------------------------------------------------------------------------- admin
def _admin(request):
    if not auth.is_admin(request):
        raise HTTPException(403, "admin only")


@router.get("/api/v1/admin/songs")
def admin_songs(request: Request, status: str = "", q: str = "", limit: int = 100):
    _admin(request)
    words = ban_words()
    sql, args = "SELECT s.*, u.nick AS owner_nick, u.banned AS owner_banned FROM songs s LEFT JOIN users u ON u.steamid = s.owner WHERE s.status != 'deleted'", []
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
        w["hidden"] = hidden(r["title"], r["artist"], words) or (r["live_version"] and hidden(r["live_title"], r["live_artist"], words))
        w["owner_banned"] = bool(r["owner_banned"])
        out.append(w)
        if len(out) >= max(1, min(limit, 500)):
            break
    return {"songs": out}


@router.post("/api/v1/admin/songs/{u}/approve")
def admin_approve(u: str, request: Request):
    """the working copy becomes the published one (the game servers switch to it)"""
    _admin(request)
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
                    "updated = ? WHERE uuid = ?", (ver, ver, _now(), _now(), u))
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
    return {"words": words[:2000]}


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
