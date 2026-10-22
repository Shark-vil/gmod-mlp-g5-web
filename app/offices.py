"""HoofTour: the players' offices from the mod (docs/OFFICE.md there), their ratings, and linking a game to Steam.

Linking the game (the office is uploaded only by its owner, proven by a Steam login):
- POST /api/v1/link                  the game asks: { steamid } -> { id, secret, words, url }
- GET  /link/<id>                    the player opens it (Steam overlay / browser), signs in with Steam and confirms;
                                     the page shows the words (e.g. APPLE-CLOUD-42) the game shows too. They are NOT a
                                     Steam Guard code and are never typed anywhere: only compared
- GET  /api/v1/link/<id>/poll        the game polls with X-Link-Secret; once confirmed it gets its token, once
- the token goes in "Authorization: Bearer"; only its hash is kept. DELETE /api/v1/link/token forgets it
The URL's id is public, the secret only the game knows: whoever sees the link can't take the token. A link made by
someone else (phishing) shows other words than the player's game, and the Steam account must be the game's one.

Offices: PUT / DELETE /api/v1/office (token), GET /api/v1/office/me (token), GET /api/v1/offices (the list),
GET /api/v1/office/<steamid>. The site doesn't know the mod's shop: item ids are only checked for their form, the
game drops unknown items and applies the per-item limits when it shows an office. Positions are relative to the
office's origin (any map).

Ratings: POST /api/v1/office/<steamid>/rate { stars 1..5, voter }. With a token the voter is proven; without, the
game's SteamID is taken on trust, so one vote per office per voter AND per IP, a few votes and new voters a day per
IP. The list's "top" is a Bayesian average (a lone 5 doesn't lead).
"""
import hashlib
import json
import math
import re
import secrets
import time

from fastapi import APIRouter, Header, HTTPException, Request

try:
    from . import auth
except ImportError:
    import auth

router = APIRouter()
_db = None
_ip = None
_rate = None

MAX_PROPS = 60                  # a bit over the mod's default mlp_office_max_props (40)
MAX_BODY = 16384
MAX_COORD = 2000.0              # office units from its origin
UPLOADS_PER_MIN = 6
UPLOADS_PER_DAY = 100
LINKS_PER_MIN_IP = 6
LINKS_PER_DAY_IP = 40
LINK_TTL = 600                  # s to confirm, then the game must ask again
TOKENS_PER_PLAYER = 5           # several PCs; the oldest go
VOTES_PER_MIN_IP = 10
VOTES_PER_DAY_IP = 30
VOTERS_PER_DAY_IP = 20          # new voter SteamIDs per IP a day
TOP_PRIOR, TOP_WEIGHT = 3.0, 3  # Bayesian average: as if 3 votes of 3 stars were there
PER_PAGE = 12

SID_RE = re.compile(r"^\d{17}$")
ITEM_RE = re.compile(r"^[A-Za-z0-9_]{1,48}$")
MAP_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
# the office's wall / floor finish: "wall:colour:floor:colour" (the game checks the ids against its own list)
STYLE_RE = re.compile(r"^[a-z0-9]{1,16}:\d{1,2}:[a-z0-9]{1,16}:\d{1,2}$")
LINK_RE = re.compile(r"^[0-9a-f]{16}$")
WORDS = ["APPLE", "CLOUD", "STAR", "HOOF", "MANE", "TAIL", "WING", "HORN", "CAKE", "MOON", "SUN", "BAY", "SHELL",
         "WAVE", "BERRY", "PEAR", "GEM", "BELL", "KITE", "LAMP", "ROSE", "LEAF", "SMOOTHIE", "SKATE", "MAGIC", "CRYSTAL",
         "RAINBOW", "LIGHTHOUSE", "FEATHER", "COOKIE", "SPARKLE", "HONEY"]


def init(db_factory, client_ip, rate_ok):
    global _db, _ip, _rate
    _db, _ip, _rate = db_factory, client_ip, rate_ok
    with _db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS link_codes ( id TEXT PRIMARY KEY, secret TEXT, words TEXT, game_sid TEXT, ip TEXT,
                created INTEGER, steamid TEXT, token TEXT );
            CREATE INDEX IF NOT EXISTS idx_link_ip ON link_codes ( ip, created );
            CREATE TABLE IF NOT EXISTS game_tokens ( hash TEXT PRIMARY KEY, steamid TEXT, created INTEGER, last_used INTEGER );
            CREATE INDEX IF NOT EXISTS idx_tokens_sid ON game_tokens ( steamid );
            CREATE TABLE IF NOT EXISTS offices ( steamid TEXT PRIMARY KEY, map TEXT, props INTEGER, data TEXT, created INTEGER,
                updated INTEGER, day INTEGER, uploads INTEGER, rating_sum INTEGER DEFAULT 0, rating_n INTEGER DEFAULT 0 );
            CREATE TABLE IF NOT EXISTS office_votes ( owner TEXT, voter TEXT, stars INTEGER, ip TEXT, ts INTEGER, first_ts INTEGER,
                verified INTEGER, PRIMARY KEY ( owner, voter ) );
            CREATE INDEX IF NOT EXISTS idx_votes_ip ON office_votes ( ip, ts );
            """
        )
        if "style" not in [c[1] for c in con.execute("PRAGMA table_info(offices)")]:
            con.execute("ALTER TABLE offices ADD COLUMN style TEXT DEFAULT ''")


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _check_ip(request, bucket, per_min):
    ip = _ip(request)
    if not _rate(f"{bucket}:{ip}", per_min):
        raise HTTPException(429, "too many requests")
    return ip


def _token_sid(authorization: str):
    """The SteamID behind a game token, or None."""
    if not authorization.startswith("Bearer "):
        return None
    tok = authorization[7:].strip()
    if not 20 <= len(tok) <= 100:
        return None
    with _db() as con:
        row = con.execute("SELECT steamid FROM game_tokens WHERE hash = ?", (_hash(tok),)).fetchone()
        if not row:
            return None
        con.execute("UPDATE game_tokens SET last_used = ? WHERE hash = ?", (int(time.time()), _hash(tok)))
        return row["steamid"]


def _require_token(authorization: str):
    sid = _token_sid(authorization)
    if not sid:
        raise HTTPException(401, "link the game first")
    with _db() as con:
        u = con.execute("SELECT banned FROM users WHERE steamid = ?", (sid,)).fetchone()
    if u and u["banned"]:
        raise HTTPException(403, "banned")
    return sid


async def _json(request: Request):
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(413, "too big")
    try:
        body = json.loads(raw or b"{}")
    except Exception:
        raise HTTPException(400, "bad json")
    if not isinstance(body, dict):
        raise HTTPException(400, "bad json")
    return body


def _rating(row):
    n = row["rating_n"] or 0
    return round(row["rating_sum"] / n, 2) if n else 0, n


def _who(con, sid):
    """Nick and avatar: the Steam login's profile, else the nick the game servers reported."""
    u = con.execute("SELECT nick, avatar FROM users WHERE steamid = ?", (sid,)).fetchone()
    p = con.execute("SELECT nick FROM players WHERE steamid = ?", (sid,)).fetchone()
    return (u and u["nick"]) or (p and p["nick"]) or sid, (u and u["avatar"]) or ""


# ---------------------------------------------------------------------------------------------- linking the game

@router.post("/api/v1/link")
async def link_start(request: Request):
    ip = _check_ip(request, "link", LINKS_PER_MIN_IP)
    body = await _json(request)
    game_sid = str(body.get("steamid", ""))
    if not SID_RE.match(game_sid):
        raise HTTPException(400, "bad steamid")
    now = int(time.time())
    with _db() as con:
        con.execute("DELETE FROM link_codes WHERE created < ?", (now - LINK_TTL,))
        if con.execute("SELECT COUNT(*) FROM link_codes WHERE ip = ? AND created > ?", (ip, now - 86400)).fetchone()[0] >= LINKS_PER_DAY_IP:
            raise HTTPException(429, "too many links today")
        lid, secret = secrets.token_hex(8), secrets.token_urlsafe(24)
        words = f"{secrets.choice(WORDS)}-{secrets.choice(WORDS)}-{secrets.randbelow(90) + 10}"
        con.execute("INSERT INTO link_codes ( id, secret, words, game_sid, ip, created ) VALUES ( ?, ?, ?, ?, ?, ? )",
                    (lid, _hash(secret), words, game_sid, ip, now))
    return {"id": lid, "secret": secret, "words": words, "url": f"{auth._base(request)}/link/{lid}", "expires": LINK_TTL}


def _link(con, lid):
    if not LINK_RE.match(lid):
        raise HTTPException(404, "no such link")
    row = con.execute("SELECT * FROM link_codes WHERE id = ?", (lid,)).fetchone()
    if not row or row["created"] < time.time() - LINK_TTL:
        raise HTTPException(404, "the link expired: press the button in the game again")
    return row


@router.get("/api/v1/link/{lid}/info")
def link_info(lid: str, request: Request):
    """For the confirm page: the words, which account the game is, who is signed in here."""
    _check_ip(request, "linkinfo", 60)
    with _db() as con:
        row = _link(con, lid)
        nick, avatar = _who(con, row["game_sid"])
    u = auth.current_user(request)
    return {"words": row["words"], "game_steamid": row["game_sid"], "game_nick": nick, "confirmed": bool(row["steamid"]),
            "me": u and {"steamid": u["steamid"], "nick": u["nick"], "avatar": u["avatar"]}}


@router.post("/api/v1/link/{lid}/confirm")
def link_confirm(lid: str, request: Request):
    u = auth.require_user(request)  # the session cookie is SameSite=Lax: no cross-site POST
    with _db() as con:
        row = _link(con, lid)
        if row["steamid"]:
            return {"ok": True}
        if row["game_sid"] != u["steamid"]:
            raise HTTPException(409, "other account")
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        con.execute("INSERT INTO game_tokens ( hash, steamid, created, last_used ) VALUES ( ?, ?, ?, ? )", (_hash(token), u["steamid"], now, now))
        old = con.execute("SELECT hash FROM game_tokens WHERE steamid = ? ORDER BY created DESC LIMIT -1 OFFSET ?",
                          (u["steamid"], TOKENS_PER_PLAYER)).fetchall()
        for r in old:
            con.execute("DELETE FROM game_tokens WHERE hash = ?", (r["hash"],))
        con.execute("UPDATE link_codes SET steamid = ?, token = ? WHERE id = ?", (u["steamid"], token, lid))
    return {"ok": True}


@router.get("/api/v1/link/{lid}/poll")
def link_poll(lid: str, request: Request, x_link_secret: str = Header(default="")):
    _check_ip(request, "linkpoll", 60)
    with _db() as con:
        if not LINK_RE.match(lid):
            return {"state": "expired"}
        row = con.execute("SELECT * FROM link_codes WHERE id = ?", (lid,)).fetchone()
        if not row or not secrets.compare_digest(row["secret"], _hash(x_link_secret)):
            return {"state": "expired"}
        if not row["token"]:
            return {"state": "expired" if row["created"] < time.time() - LINK_TTL else "wait"}
        con.execute("DELETE FROM link_codes WHERE id = ?", (lid,))  # the token is handed out once
        nick, _ = _who(con, row["steamid"])
    return {"state": "ok", "token": row["token"], "steamid": row["steamid"], "nick": nick}


@router.delete("/api/v1/link/token")
def link_forget(authorization: str = Header(default="")):
    if authorization.startswith("Bearer "):
        with _db() as con:
            con.execute("DELETE FROM game_tokens WHERE hash = ?", (_hash(authorization[7:].strip()),))
    return {"ok": True}


# ---------------------------------------------------------------------------------------------- offices

def _num(v, lim):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > lim:
        return None
    return round(float(v), 2)


@router.put("/api/v1/office")
async def office_put(request: Request, authorization: str = Header(default="")):
    """{ map, props: [ [ item, x, y, z, pitch, yaw, roll ], … ], style? } (relative to the office's origin)."""
    _check_ip(request, "office", 30)
    sid = _require_token(authorization)
    if not _rate("officeput:" + sid, UPLOADS_PER_MIN):
        raise HTTPException(429, "too many uploads")
    body = await _json(request)
    mp = str(body.get("map", ""))
    props = body.get("props")
    if not MAP_RE.match(mp) or not isinstance(props, list):
        raise HTTPException(400, "bad office")
    if len(props) > MAX_PROPS:
        raise HTTPException(400, f"at most {MAX_PROPS} things")
    style = body.get("style", "")
    if not isinstance(style, str) or style and not STYLE_RE.match(style):
        raise HTTPException(400, "bad style")
    clean = []
    for p in props:
        if not isinstance(p, list) or len(p) != 7 or not isinstance(p[0], str) or not ITEM_RE.match(p[0]):
            raise HTTPException(400, "bad thing")
        nums = [_num(v, MAX_COORD) for v in p[1:4]] + [_num(v, 360) for v in p[4:7]]
        if None in nums:
            raise HTTPException(400, "bad position")
        clean.append([p[0]] + nums)
    now = int(time.time())
    day = now // 86400
    with _db() as con:
        row = con.execute("SELECT day, uploads FROM offices WHERE steamid = ?", (sid,)).fetchone()
        uploads = row["uploads"] + 1 if row and row["day"] == day else 1
        if uploads > UPLOADS_PER_DAY:
            raise HTTPException(429, "too many uploads today")
        con.execute(
            "INSERT INTO offices ( steamid, map, props, data, style, created, updated, day, uploads ) VALUES ( ?, ?, ?, ?, ?, ?, ?, ?, ? ) "
            "ON CONFLICT ( steamid ) DO UPDATE SET map = excluded.map, props = excluded.props, data = excluded.data, "
            "style = excluded.style, updated = excluded.updated, day = excluded.day, uploads = excluded.uploads",
            (sid, mp, len(clean), json.dumps(clean, separators=(",", ":")), style, now, now, day, uploads),
        )
    return {"ok": True, "props": len(clean)}


@router.delete("/api/v1/office")
def office_delete(request: Request, authorization: str = Header(default="")):
    _check_ip(request, "office", 30)
    sid = _require_token(authorization)
    with _db() as con:
        con.execute("DELETE FROM offices WHERE steamid = ?", (sid,))
    return {"ok": True}


@router.get("/api/v1/office/me")
def office_me(request: Request, authorization: str = Header(default="")):
    """Is the token still good, and what of mine is on the site."""
    _check_ip(request, "office", 30)
    sid = _require_token(authorization)
    with _db() as con:
        nick, avatar = _who(con, sid)
        row = con.execute("SELECT * FROM offices WHERE steamid = ?", (sid,)).fetchone()
    out = {"steamid": sid, "nick": nick, "uploaded": bool(row)}
    if row:
        out["props"], out["updated"] = row["props"], row["updated"]
        out["rating"], out["votes"] = _rating(row)
    return out


@router.get("/api/v1/offices")
def office_list(request: Request, sort: str = "top", page: int = 1):
    _check_ip(request, "offices", 60)
    order = {
        "top": f"( rating_sum + {TOP_PRIOR * TOP_WEIGHT} ) / ( rating_n + {TOP_WEIGHT} ) DESC, rating_n DESC, updated DESC",
        "new": "updated DESC",
        "big": "props DESC, updated DESC",
    }.get(sort)
    if not order:
        raise HTTPException(400, "bad sort")
    with _db() as con:
        total = con.execute("SELECT COUNT(*) FROM offices WHERE props > 0").fetchone()[0]
        pages = max(1, math.ceil(total / PER_PAGE))
        page = max(1, min(page, pages))
        rows = con.execute(f"SELECT * FROM offices WHERE props > 0 ORDER BY {order} LIMIT ? OFFSET ?",
                           (PER_PAGE, (page - 1) * PER_PAGE)).fetchall()
        out = []
        for r in rows:
            nick, avatar = _who(con, r["steamid"])
            rating, votes = _rating(r)
            out.append({"steamid": r["steamid"], "nick": nick, "avatar": avatar, "props": r["props"], "rating": rating,
                        "votes": votes, "updated": r["updated"]})
    return {"total": total, "page": page, "pages": pages, "offices": out}


@router.get("/api/v1/office/{steamid}")
def office_get(steamid: str, request: Request, voter: str = ""):
    """One office's things; with ?voter=<steamid> also that voter's stars for it."""
    _check_ip(request, "offices", 60)
    if not SID_RE.match(steamid):
        raise HTTPException(400, "bad steamid")
    with _db() as con:
        row = con.execute("SELECT * FROM offices WHERE steamid = ?", (steamid,)).fetchone()
        if not row:
            raise HTTPException(404, "no office")
        nick, avatar = _who(con, steamid)
        mine = SID_RE.match(voter) and con.execute("SELECT stars FROM office_votes WHERE owner = ? AND voter = ?", (steamid, voter)).fetchone()
    rating, votes = _rating(row)
    return {"steamid": steamid, "nick": nick, "avatar": avatar, "map": row["map"], "props": json.loads(row["data"]),
            "style": row["style"] or "", "rating": rating, "votes": votes, "updated": row["updated"], "my_vote": mine["stars"] if mine else 0}


@router.post("/api/v1/office/{steamid}/rate")
async def office_rate(steamid: str, request: Request, authorization: str = Header(default="")):
    ip = _check_ip(request, "rate", VOTES_PER_MIN_IP)
    body = await _json(request)
    stars = body.get("stars")
    if not SID_RE.match(steamid) or isinstance(stars, bool) or not isinstance(stars, int) or not 1 <= stars <= 5:
        raise HTTPException(400, "bad vote")
    voter = _token_sid(authorization)
    verified = 1 if voter else 0
    voter = voter or str(body.get("voter", ""))
    if not SID_RE.match(voter):
        raise HTTPException(400, "bad voter")
    if voter == steamid:
        raise HTTPException(403, "not your own office")
    now = int(time.time())
    today = now - now % 86400
    with _db() as con:
        u = con.execute("SELECT banned FROM users WHERE steamid = ?", (voter,)).fetchone()
        if u and u["banned"]:
            raise HTTPException(403, "banned")
        if not con.execute("SELECT 1 FROM offices WHERE steamid = ?", (steamid,)).fetchone():
            raise HTTPException(404, "no office")
        old = con.execute("SELECT stars FROM office_votes WHERE owner = ? AND voter = ?", (steamid, voter)).fetchone()
        if not old:
            # one vote per office per network, unless the voter is proven
            if not verified and con.execute("SELECT 1 FROM office_votes WHERE owner = ? AND ip = ? AND voter != ?", (steamid, ip, voter)).fetchone():
                raise HTTPException(429, "already rated from this network")
            if con.execute("SELECT COUNT(*) FROM office_votes WHERE ip = ? AND first_ts >= ?", (ip, today)).fetchone()[0] >= VOTES_PER_DAY_IP:
                raise HTTPException(429, "too many votes today")
            voters = con.execute("SELECT COUNT(DISTINCT voter) FROM office_votes WHERE ip = ? AND first_ts >= ?", (ip, today)).fetchone()[0]
            seen = con.execute("SELECT 1 FROM office_votes WHERE ip = ? AND voter = ? AND first_ts >= ?", (ip, voter, today)).fetchone()
            if not verified and not seen and voters >= VOTERS_PER_DAY_IP:
                raise HTTPException(429, "too many voters from this network")
            con.execute("INSERT INTO office_votes ( owner, voter, stars, ip, ts, first_ts, verified ) VALUES ( ?, ?, ?, ?, ?, ?, ? )",
                        (steamid, voter, stars, ip, now, now, verified))
            con.execute("UPDATE offices SET rating_sum = rating_sum + ?, rating_n = rating_n + 1 WHERE steamid = ?", (stars, steamid))
        else:
            con.execute("UPDATE office_votes SET stars = ?, ip = ?, ts = ?, verified = MAX( verified, ? ) WHERE owner = ? AND voter = ?",
                        (stars, ip, now, verified, steamid, voter))
            con.execute("UPDATE offices SET rating_sum = rating_sum + ? WHERE steamid = ?", (stars - old["stars"], steamid))
        row = con.execute("SELECT rating_sum, rating_n FROM offices WHERE steamid = ?", (steamid,)).fetchone()
    rating, votes = _rating(row)
    return {"ok": True, "rating": rating, "votes": votes, "my_vote": stars}


# ---------------------------------------------------------------------------------------------- admin

@router.delete("/api/v1/admin/office/{steamid}")
def admin_office_delete(steamid: str, request: Request, votes_only: bool = False):
    """Remove an office (and its votes), or with ?votes_only=1 just reset its rating."""
    if not auth.is_admin(request):
        raise HTTPException(403, "admin only")
    with _db() as con:
        con.execute("DELETE FROM office_votes WHERE owner = ?", (steamid,))
        if votes_only:
            con.execute("UPDATE offices SET rating_sum = 0, rating_n = 0 WHERE steamid = ?", (steamid,))
        else:
            con.execute("DELETE FROM offices WHERE steamid = ?", (steamid,))
    return {"ok": True}
