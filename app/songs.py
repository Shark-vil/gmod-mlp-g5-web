"""Dance songs from players (the mod's PLANS.md 13): upload, analysis, storage.

A logged-in player uploads a song (mp3 / ogg / wav / flac / m4a / opus; MLP_SONG_MAX_MB, MLP_SONG_MAX_SEC);
the server transcodes it to ogg without metadata (ffmpeg), finds the tempo and makes a first note chart
(chart_auto.py, in the background, one song at a time) and a short preview fragment.
The FULL track is kept only while the song is a draft or waiting for the audit, and only its author and the
admins can hear it; once it is approved / rejected / deleted the full track is erased (purge_full) and only the
preview, the notes and the facts stay.

  POST   /api/v1/songs                  multipart: file, title, artist   -> { uuid }
  GET    /api/v1/songs/mine             the player's own songs
  GET    /api/v1/songs/{uuid}           facts + notes (+ beat grid for the author / admins)
  GET    /api/v1/songs/{uuid}/full.ogg  the full track (author / admins, while draft / pending)
  GET    /api/v1/songs/{uuid}/preview.ogg
  POST   /api/v1/songs/{uuid}/auto      redo the automatic chart (author, draft)
  DELETE /api/v1/songs/{uuid}           author (draft) or admin
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid as uuidlib

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

try:
    from . import auth, chart_auto
except ImportError:
    import auth
    import chart_auto

MAX_MB = float(os.environ.get("MLP_SONG_MAX_MB", "15"))
MAX_SEC = float(os.environ.get("MLP_SONG_MAX_SEC", "360"))
MIN_SEC = 20
MAX_DRAFTS = int(os.environ.get("MLP_SONG_MAX_DRAFTS", "5"))
UPLOADS_PER_HOUR = int(os.environ.get("MLP_SONG_UPLOADS_PER_HOUR", "10"))
PREVIEW_LEN = 20
EXTS = {".mp3", ".ogg", ".wav", ".flac", ".m4a", ".opus"}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
LIVE = ("draft", "pending")  # the states that keep the full track

router = APIRouter()
_db = None
_dir = None
_work = threading.Semaphore(1)  # one analysis at a time (librosa is heavy)


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
            """
        )


def song_dir(u):
    return os.path.join(_dir, u)


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


def make_preview(u, start, length):
    """the public fragment: length seconds from start, a short fade in / out"""
    full = os.path.join(song_dir(u), "full.ogg")
    out = os.path.join(song_dir(u), "preview.ogg")
    fade = min(1.0, length / 4)
    _ffmpeg(["-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", full, "-af",
             f"afade=t=in:d={fade:.2f},afade=t=out:st={max(0, length - fade):.2f}:d={fade:.2f}",
             "-map_metadata", "-1", "-c:a", "libvorbis", "-q:a", "3", out])


def _analyse(u):
    with _work:
        with _db() as con:
            con.execute("UPDATE songs SET analysis = 'running', updated = ? WHERE uuid = ?", (int(time.time()), u))
        try:
            res = chart_auto.analyze(os.path.join(song_dir(u), "full.ogg"))
            start = round(max(0.0, min(res["len"] - PREVIEW_LEN, res["len"] * 0.3)), 2)
            length = round(min(PREVIEW_LEN, res["len"]), 2)
            make_preview(u, start, length)
            with _db() as con:
                con.execute("UPDATE songs SET analysis = 'done', analysis_error = NULL, len = ?, bpm = ?, beat = ?, beats = ?, chart = ?, "
                            "preview_start = COALESCE( preview_start, ? ), preview_len = COALESCE( preview_len, ? ), updated = ? WHERE uuid = ?",
                            (res["len"], res["bpm"], res["beat"], json.dumps(res["beats"]), json.dumps(res["chart"]), start, length, int(time.time()), u))
        except Exception as e:
            with _db() as con:
                con.execute("UPDATE songs SET analysis = 'failed', analysis_error = ?, updated = ? WHERE uuid = ?", (str(e)[:300], int(time.time()), u))
        finally:
            # deleted while it was being analysed: clean up after ourselves (the files were in use then)
            with _db() as con:
                r = con.execute("SELECT status FROM songs WHERE uuid = ?", (u,)).fetchone()
            if r and r["status"] == "deleted":
                shutil.rmtree(song_dir(u), ignore_errors=True)


def start_analysis(u):
    threading.Thread(target=_analyse, args=(u,), daemon=True).start()


def purge_full(u):
    """the audit is over (approved / rejected / deleted): the full track goes, the preview stays"""
    p = os.path.join(song_dir(u), "full.ogg")
    if os.path.exists(p):
        os.remove(p)


def _row(u):
    if not UUID_RE.match(u or ""):
        raise HTTPException(404, "no such song")
    with _db() as con:
        r = con.execute("SELECT * FROM songs WHERE uuid = ?", (u,)).fetchone()
    if not r or r["status"] == "deleted":
        raise HTTPException(404, "no such song")
    return r


def _can_edit(request, r):
    u = auth.current_user(request)
    return bool(u and (u["admin"] or u["steamid"] == r["owner"]))


def public(r, full=False):
    out = {"uuid": r["uuid"], "title": r["title"], "artist": r["artist"], "status": r["status"], "owner": r["owner"],
           "owner_profile": f"https://steamcommunity.com/profiles/{r['owner']}", "len": r["len"], "bpm": r["bpm"], "beat": r["beat"],
           "preview_start": r["preview_start"], "preview_len": r["preview_len"], "wsid": r["wsid"],
           "analysis": r["analysis"], "created": r["created"], "updated": r["updated"],
           "chart": json.loads(r["chart"]) if r["chart"] else None}
    if full:
        out["beats"] = json.loads(r["beats"]) if r["beats"] else []
        out["analysis_error"] = r["analysis_error"]
        out["reason"] = r["reason"]
        out["has_full"] = os.path.exists(os.path.join(song_dir(r["uuid"]), "full.ogg"))
    return out


@router.post("/api/v1/songs")
def upload(request: Request, file: UploadFile = File(...), title: str = Form(...), artist: str = Form("")):
    user = auth.require_user(request)
    title, artist = title.strip()[:80], artist.strip()[:80]
    if not title:
        raise HTTPException(400, "a title is needed")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in EXTS:
        raise HTTPException(400, "mp3, ogg, wav, flac, m4a or opus")
    now = int(time.time())
    with _db() as con:
        drafts = con.execute("SELECT COUNT(*) AS n FROM songs WHERE owner = ? AND status = 'draft'", (user["steamid"],)).fetchone()["n"]
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
                     os.path.join(song_dir(u), "full.ogg")])
        except Exception:
            shutil.rmtree(song_dir(u), ignore_errors=True)
            raise HTTPException(400, "the file couldn't be converted")
    with _db() as con:
        con.execute("INSERT INTO songs ( uuid, owner, title, artist, status, created, updated, len, analysis ) VALUES ( ?, ?, ?, ?, 'draft', ?, ?, ?, 'queued' )",
                    (u, user["steamid"], title, artist, now, now, round(length, 2)))
    start_analysis(u)
    return {"uuid": u}


@router.get("/api/v1/songs/mine")
def mine(request: Request):
    user = auth.require_user(request)
    with _db() as con:
        rows = con.execute("SELECT * FROM songs WHERE owner = ? AND status != 'deleted' ORDER BY updated DESC", (user["steamid"],)).fetchall()
    return {"songs": [public(r) for r in rows]}


@router.get("/api/v1/songs/{u}")
def song(u: str, request: Request):
    r = _row(u)
    editor = _can_edit(request, r)
    if r["status"] != "approved" and not editor:
        raise HTTPException(404, "no such song")
    return public(r, full=editor)


@router.get("/api/v1/songs/{u}/full.ogg")
def full_audio(u: str, request: Request):
    r = _row(u)
    path = os.path.join(song_dir(u), "full.ogg")
    if not _can_edit(request, r) or r["status"] not in LIVE or not os.path.exists(path):
        raise HTTPException(404, "no full track")
    return FileResponse(path, media_type="audio/ogg", headers={"Cache-Control": "private, no-store"})


@router.get("/api/v1/songs/{u}/preview.ogg")
def preview_audio(u: str, request: Request):
    r = _row(u)
    path = os.path.join(song_dir(u), "preview.ogg")
    if (r["status"] != "approved" and not _can_edit(request, r)) or not os.path.exists(path):
        raise HTTPException(404, "no preview")
    return FileResponse(path, media_type="audio/ogg")


@router.post("/api/v1/songs/{u}/auto")
def redo_auto(u: str, request: Request):
    r = _row(u)
    if not _can_edit(request, r) or r["status"] != "draft":
        raise HTTPException(403, "only the author's draft")
    if not os.path.exists(os.path.join(song_dir(u), "full.ogg")):
        raise HTTPException(409, "no full track")
    with _db() as con:
        con.execute("UPDATE songs SET analysis = 'queued', updated = ? WHERE uuid = ?", (int(time.time()), u))
    start_analysis(u)
    return {"ok": True}


@router.delete("/api/v1/songs/{u}")
def delete(u: str, request: Request):
    r = _row(u)
    user = auth.require_user(request)
    if not (user["admin"] or (user["steamid"] == r["owner"] and r["status"] == "draft")):
        raise HTTPException(403, "only the author's draft (or an admin)")
    with _db() as con:
        con.execute("UPDATE songs SET status = 'deleted', updated = ? WHERE uuid = ?", (int(time.time()), u))
    shutil.rmtree(song_dir(u), ignore_errors=True)  # an analysis still running finishes the job (see _analyse)
    return {"ok": True}
