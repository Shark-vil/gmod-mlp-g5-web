"""MLP: Maretime Bay — stats / unlocks service for the GMod mod.

Any game server may report — a dedicated server or a player's own single-player game: there are no keys
to hand out. Every GMod install makes up its own random id (X-Install-Id) and reports snapshots of its
players' saved data; the service keeps one copy per (install, player) and serves aggregated leaderboards
and the unlocked items / spells (other servers read them back).

Aggregation across installs: counters are summed, "best_*" keys take the maximum, "time_*" the minimum.

Anti-cheat is deliberately light — it only stops the obvious:
- rate limits per install (a whole server, any number of players, is one install sending batches) and a
  generous flood limit per IP (one IP may be a big server, a hoster with many servers, players behind NAT);
  a limited number of new installs per IP a day (no farming of fresh ids)
- every counter may grow only so fast per install (since that install's last report) AND only so much
  per player per day over all installs together (fresh installs can't multiply it)
- impossible race times are ignored
- unlocks must make sense: "bits" items need the stars, a spell needs the one of the tier below, only so
  many new unlocks per player a day
- a fresh install starts from a small allowance (one report can't bring a day's worth), and unlocks are
  tied to the play time the player has (a couple per ten minutes)
- POSTs need a one-time nonce (GET /api/v1/nonce, lives a few seconds, for one purpose and one install) and a
  signature SHA256( nonce .. body .. salt ) - the salt is in the mod's code, so this only makes hand-made
  requests awkward (no "Try it out" cheating, no replays); /api/docs is for admins only (Steam login)
- everything clamped / refused is logged in `flags`; admins (MLP_ADMIN_TOKEN) can list the flags and wipe a
  player or an install
Optional MLP_SERVER_KEYS ("name:key,…") marks trusted servers (named, no per-IP install limit).
"""
import hashlib
import hmac
import json
import math
import secrets
import os
import re
import sqlite3
import threading
import time
from collections import defaultdict, deque

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

try:  # in the image: the package "app"; in local tests: the folder itself
    from . import auth, songs
except ImportError:
    import auth
    import songs

DB_PATH = os.environ.get("MLP_DB", "/data/stats.db")
SERVER_KEYS = {}
for pair in filter(None, (p.strip() for p in os.environ.get("MLP_SERVER_KEYS", "").split(","))):
    name, _, key = pair.partition(":")
    if key:
        SERVER_KEYS[key] = name
ADMIN_TOKEN = os.environ.get("MLP_ADMIN_TOKEN", "")
RATE_PER_MIN = int(os.environ.get("MLP_RATE_PER_MIN", "30"))         # reports per install
LOOKUP_PER_MIN = int(os.environ.get("MLP_LOOKUP_PER_MIN", "60"))     # unlock lookups (batches) per install
IP_RATE_PER_MIN = int(os.environ.get("MLP_IP_RATE_PER_MIN", "300"))  # any requests per IP: flood guard only
NEW_INSTALLS_PER_IP_DAY = int(os.environ.get("MLP_NEW_INSTALLS_PER_IP_DAY", "20"))  # hosters run many servers on one IP
MAX_LOOKUP = 128
# must match MLP.ApiSalt in the mod (lua/mlp_g5_lib/sv_stats_api.lua)
SIGN_SALT = os.environ.get("MLP_SIGN_SALT", "mlp-g5:maretime-bay:9f3c1a7e")
NONCE_TTL = float(os.environ.get("MLP_NONCE_TTL", "5"))  # the mod sends the POST right after getting it
NONCE_PURPOSES = ("report", "unlocks")
MAX_NONCES_PER_INSTALL = 8
# unlocks a player may have for their play time: a few to start with, then one per this many minutes
UNLOCK_FREE = 3
UNLOCK_MINUTES = 5

MAX_PLAYERS = 128
MAX_STATS = 96
KEY_RE = re.compile(r"^[a-z0-9_]{1,40}$")
SID_RE = re.compile(r"^\d{1,20}$")
ITEM_RE = re.compile(r"^[A-Za-z0-9_]{1,48}$")
INSTALL_RE = re.compile(r"^[0-9a-f]{32}$")
UNLOCK_KINDS = ("vanity", "spell")
MAX_UNLOCKS = 128

# fastest believable growth per minute since this install's last report (plus a small allowance)
# (per minute, allowance: also where a fresh install starts from)
GROWTH = {"stars": (150, 300), "vanity": (10, 40), "playtime": (70, 3600)}
GROWTH_DEFAULT = (20, 50)
# the most a player may gain per day over all installs together (by key, then by prefix)
DAILY = {"stars": 2500, "playtime": 86400, "vanity": 40, "spells": 6, "botwins": 300, "pvpwins": 300}
DAILY_PREFIX = {"games_": 500, "wins_": 300, "botraces_": 500, "botwins_": 300, "pvpgames_": 500, "pvpwins_": 300}
DAILY_DEFAULT = 500
# a race can't be won faster than this (seconds)
MIN_TIME = {"time_skyrace": 6, "time_groundrace": 4}
MIN_TIME_DEFAULT = 3
# a best score can't be higher than this (the dance minigame: every note perfect at the top combo ≈ 1300)
# rampage: 300 s at most, a hit every ~0.4 s at ×2 combo ≈ 15000
MAX_BEST = {"best_dance": 1500, "best_rampage": 20000}
# kept per day for the charts (GET /api/v1/history/{steamid})
HISTORY_KEYS = ("stars", "playtime")
HISTORY_MAX_DAYS = 120

# the game's collectibles: "bits" items need this many all-time stars (game value × half the mod's default
# mlp_vanity_scale of 0.1 — servers may lower it); the others are gifts / defaults
VANITY_BITS = {
    "AlicornWingsV1": 1936, "FakeWingsV2": 1550, "FloatV1": 1615, "ShirtCollarV1": 1357, "Glasses01V1": 650,
    "Glasses01V2": 925, "Glasses02V1": 1679, "Glasses02V2": 1164, "GrannyGlassesV2": 1422, "PartyGlassesV2": 1807,
    "BeanieV2": 1100, "FakeHornV1": 1486, "IzzyHatV1": 1229, "PearlEarringsV1": 714, "SoldierHelmetV1": 975,
    "WindmillHornV1": 1872, "ChokerV1": 1743, "CutieMarkV1": 875, "Scarf01V1": 1036, "Scarf01V2": 790, "Scarf02V2": 1293,
}
VANITY_QUEST = {"FakeWingsV1", "PegasiNecklaceV1", "SunnyPurseV1", "SunnyPurseV2", "GrannyGlassesV1", "PartyGlassesV1",
                "AlicornHornV1", "BeanieV1", "FashionMarkV1", "FlowerNecklaceV1", "Scarf02V1"}
MIN_SCALE = 0.05
SPELL_TIER = {"telekinesis": 2, "shield": 3, "bolt": 3, "levitate": 4, "teleport": 5}  # light (tier 1) is never stored
# a long-time player catching up gets the rest over the next reports (the mod resends everything)
NEW_UNLOCKS_PER_DAY = {"vanity": 16, "spell": 5}

# the API docs are behind the admin login (below), not public
app = FastAPI(title="MLP Maretime Bay stats", docs_url=None, redoc_url=None, openapi_url=None)
lock = threading.Lock()
hits = defaultdict(deque)
nonces = {}  # nonce -> (install, purpose, expires); one process (see Dockerfile), lost on restart: fine


def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def init():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS servers ( name TEXT PRIMARY KEY, last_seen INTEGER, map TEXT );
            CREATE TABLE IF NOT EXISTS installs ( id TEXT PRIMARY KEY, ip TEXT, first_seen INTEGER, last_seen INTEGER, title TEXT );
            CREATE TABLE IF NOT EXISTS players ( steamid TEXT PRIMARY KEY, nick TEXT, first_seen INTEGER, last_seen INTEGER, last_server TEXT );
            CREATE TABLE IF NOT EXISTS server_stats ( server TEXT, steamid TEXT, key TEXT, value REAL, updated INTEGER,
                PRIMARY KEY ( server, steamid, key ) );
            CREATE TABLE IF NOT EXISTS server_items ( server TEXT, steamid TEXT, kind TEXT, count INTEGER, PRIMARY KEY ( server, steamid, kind ) );
            CREATE TABLE IF NOT EXISTS unlocks ( steamid TEXT, kind TEXT, item TEXT, server TEXT, ts INTEGER, PRIMARY KEY ( steamid, kind, item ) );
            CREATE TABLE IF NOT EXISTS budget ( steamid TEXT, key TEXT, day INTEGER, gained REAL, PRIMARY KEY ( steamid, key, day ) );
            CREATE TABLE IF NOT EXISTS characters ( steamid TEXT, character TEXT, PRIMARY KEY ( steamid, character ) );
            CREATE TABLE IF NOT EXISTS flags ( ts INTEGER, server TEXT, steamid TEXT, key TEXT, old REAL, new REAL, kept REAL );
            CREATE INDEX IF NOT EXISTS idx_stats_key ON server_stats ( key );
            CREATE INDEX IF NOT EXISTS idx_installs_ip ON installs ( ip, first_seen );
            CREATE TABLE IF NOT EXISTS stats_daily ( steamid TEXT, day TEXT, key TEXT, value REAL, PRIMARY KEY ( steamid, day, key ) );
            """
        )


init()
auth.init(db)
app.include_router(auth.router)
songs.init(db, os.path.dirname(DB_PATH) or ".")
app.include_router(songs.router)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")


def agg(key):
    if key.startswith("best_"):
        return "MAX"
    if key.startswith("time_"):
        return "MIN"
    return "SUM"


def daily_cap(key):
    if key in DAILY:
        return DAILY[key]
    for prefix, cap in DAILY_PREFIX.items():
        if key.startswith(prefix):
            return cap
    return DAILY_DEFAULT


def rate_ok(bucket, per_min):
    now = time.time()
    q = hits[bucket]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= per_min:
        return False
    q.append(now)
    return True


def client_ip(request):
    # behind Cloudflare / a reverse proxy
    for h in ("cf-connecting-ip", "x-real-ip"):
        if request.headers.get(h):
            return request.headers[h][:64]
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return request.client.host if request.client else "?"


def check_ip(request):
    ip = client_ip(request)
    if not rate_ok("ip:" + ip, IP_RATE_PER_MIN):
        raise HTTPException(429, "too many requests")
    return ip


def num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return max(0.0, min(float(v), 1e9))


def flag(con, now, server, sid, key, old, new, kept):
    con.execute("INSERT INTO flags VALUES ( ?, ?, ?, ?, ?, ?, ? )", (now, server, sid, key, old, new, kept))


def spend(con, sid, key, day, want):
    """Take up to `want` from the player's daily budget for this key; returns what was granted."""
    row = con.execute("SELECT gained FROM budget WHERE steamid = ? AND key = ? AND day = ?", (sid, key, day)).fetchone()
    gained = row["gained"] if row else 0.0
    grant = max(0.0, min(want, daily_cap(key) - gained))
    if grant > 0:
        con.execute("REPLACE INTO budget ( steamid, key, day, gained ) VALUES ( ?, ?, ?, ? )", (sid, key, day, gained + grant))
    return grant


def prune_nonces(now):
    for n in [n for n, v in nonces.items() if v[2] < now]:
        del nonces[n]


@app.get("/api/v1/nonce")
def nonce(request: Request, purpose: str = "report", x_install_id: str = Header(default="")):
    """A one-time token for the next POST of this install (MLP_NONCE_TTL seconds)."""
    check_ip(request)
    install = x_install_id.lower()
    if not INSTALL_RE.match(install):
        raise HTTPException(400, "X-Install-Id missing")
    if purpose not in NONCE_PURPOSES:
        raise HTTPException(400, "bad purpose")
    if not rate_ok("nonce:" + install, RATE_PER_MIN + LOOKUP_PER_MIN):
        raise HTTPException(429, "too many nonces")
    now = time.time()
    with lock:
        prune_nonces(now)
        mine = [n for n, v in nonces.items() if v[0] == install]
        if len(mine) >= MAX_NONCES_PER_INSTALL:
            for n in sorted(mine, key=lambda n: nonces[n][2])[: len(mine) - MAX_NONCES_PER_INSTALL + 1]:
                del nonces[n]
        n = secrets.token_hex(16)
        nonces[n] = (install, purpose, now + NONCE_TTL)
    return {"nonce": n, "ttl": NONCE_TTL}


def check_signed(install, purpose, x_nonce, x_sign, body):
    """The nonce must be fresh, unused, for this install and purpose; the signature must match the body."""
    with lock:
        v = nonces.pop(x_nonce or "", None)
    if not v or v[0] != install or v[1] != purpose or v[2] < time.time():
        raise HTTPException(409, "nonce")
    want = hashlib.sha256(x_nonce.encode() + body + SIGN_SALT.encode()).hexdigest()
    if not hmac.compare_digest(want, (x_sign or "").lower()):
        raise HTTPException(403, "bad signature")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """browsers ask for it on their own: the site's logo"""
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "logo.svg"), media_type="image/svg+xml")


@app.get("/health")
def health():
    return {"ok": True, "time": int(time.time())}


@app.post("/api/v1/report")
async def report(request: Request, x_install_id: str = Header(default=""), x_server_key: str = Header(default=""),
                 x_nonce: str = Header(default=""), x_sign: str = Header(default="")):
    ip = check_ip(request)
    trusted = x_server_key in SERVER_KEYS
    install = x_install_id.lower()
    if not INSTALL_RE.match(install):
        raise HTTPException(400, "X-Install-Id missing")
    if not rate_ok("inst:" + (x_server_key if trusted else install), RATE_PER_MIN):
        raise HTTPException(429, "too many reports")
    raw = await request.body()
    check_signed(install, "report", x_nonce, x_sign, raw)
    try:
        body = json.loads(raw)
    except Exception:
        raise HTTPException(400, "bad json")
    if not isinstance(body, dict) or not isinstance(body.get("players"), list):
        raise HTTPException(400, "players missing")
    info = body.get("server") if isinstance(body.get("server"), dict) else {}
    title = str(info.get("name") or "")[:64]
    mapname = str(info.get("map") or "")[:64]
    now = int(time.time())
    day = now // 86400
    server = ("key:" + SERVER_KEYS[x_server_key]) if trusted else ("i:" + install)
    accepted, clamped = 0, 0

    with lock, db() as con:
        if not trusted:
            known = con.execute("SELECT id FROM installs WHERE id = ?", (install,)).fetchone()
            if not known:
                fresh = con.execute("SELECT COUNT(*) AS n FROM installs WHERE ip = ? AND first_seen > ?", (ip, now - 86400)).fetchone()["n"]
                if fresh >= NEW_INSTALLS_PER_IP_DAY:
                    raise HTTPException(429, "too many new installs from this address")
                con.execute("INSERT INTO installs ( id, ip, first_seen, last_seen, title ) VALUES ( ?, ?, ?, ?, ? )", (install, ip, now, now, title))
            else:
                con.execute("UPDATE installs SET last_seen = ?, title = ?, ip = ? WHERE id = ?", (now, title, ip, install))
        con.execute("REPLACE INTO servers ( name, last_seen, map ) VALUES ( ?, ?, ? )", (server, now, mapname))

        for p in body["players"][:MAX_PLAYERS]:
            if not isinstance(p, dict):
                continue
            sid = str(p.get("steamid", ""))
            if not SID_RE.match(sid):
                continue
            nick = str(p.get("nick", ""))[:64]
            con.execute(
                "INSERT INTO players ( steamid, nick, first_seen, last_seen, last_server ) VALUES ( ?, ?, ?, ?, ? ) "
                "ON CONFLICT ( steamid ) DO UPDATE SET nick = excluded.nick, last_seen = excluded.last_seen, last_server = excluded.last_server",
                (sid, nick, now, now, server),
            )
            # ---- counters
            stats = dict(p.get("stats") or {}) if isinstance(p.get("stats"), dict) else {}
            pt = num(p.get("playtime"))
            if pt is not None:
                stats["playtime"] = pt
            for key, value in list(stats.items())[:MAX_STATS]:
                key, value = str(key), num(value)
                if value is None or not KEY_RE.match(key):
                    continue
                row = con.execute("SELECT value, updated FROM server_stats WHERE server = ? AND steamid = ? AND key = ?", (server, sid, key)).fetchone()
                fn = agg(key)
                kept = value
                if fn == "MIN":
                    if value < MIN_TIME.get(key, MIN_TIME_DEFAULT):
                        flag(con, now, server, sid, key, row["value"] if row else -1, value, -1)
                        clamped += 1
                        continue
                elif fn == "MAX":
                    if value > MAX_BEST.get(key, 1e9):
                        flag(con, now, server, sid, key, row["value"] if row else -1, value, -1)
                        clamped += 1
                        continue
                elif fn == "SUM":
                    old = row["value"] if row else 0.0
                    if value > old:
                        # this install: a fresh one starts from the allowance, then not faster than believable
                        # since its last report (a long-time player catches up over a few reports)
                        per_min, allowance = GROWTH.get(key, GROWTH_DEFAULT)
                        limit = old + allowance + (per_min * max(0.0, (now - row["updated"]) / 60) if row else 0)
                        # the player over all installs: only so much a day
                        grant = spend(con, sid, key, day, min(value, limit) - old)
                        kept = old + grant
                        if kept < value - 1e-6:
                            flag(con, now, server, sid, key, old, value, kept)
                            clamped += 1
                con.execute("REPLACE INTO server_stats ( server, steamid, key, value, updated ) VALUES ( ?, ?, ?, ?, ? )", (server, sid, key, kept, now))
            # history for the charts: the player's total (all servers) at the end of each day (UTC)
            for key in HISTORY_KEYS:
                if key in stats:
                    total = con.execute("SELECT SUM(value) AS v FROM server_stats WHERE steamid = ? AND key = ?", (sid, key)).fetchone()["v"] or 0
                    if not con.execute("SELECT 1 FROM stats_daily WHERE steamid = ? AND key = ? LIMIT 1", (sid, key)).fetchone():
                        # the first record: a baseline under yesterday, so today shows only today's growth
                        con.execute("INSERT OR IGNORE INTO stats_daily ( steamid, day, key, value ) VALUES ( ?, ?, ?, ? )",
                                    (sid, time.strftime("%Y-%m-%d", time.gmtime(now - 86400)), key, total))
                    con.execute("REPLACE INTO stats_daily ( steamid, day, key, value ) VALUES ( ?, ?, ?, ? )",
                                (sid, time.strftime("%Y-%m-%d", time.gmtime(now)), key, total))
            items = p.get("items") if isinstance(p.get("items"), dict) else {}
            for kind, count in list(items.items())[:16]:
                count = num(count)
                if count is not None and KEY_RE.match(str(kind)):
                    con.execute("REPLACE INTO server_items ( server, steamid, kind, count ) VALUES ( ?, ?, ?, ? )", (server, sid, str(kind), int(count)))
            # ---- unlocks: kept forever, shared with every server; only ones that make sense
            unlocked = p.get("unlocked") if isinstance(p.get("unlocked"), dict) else {}
            if unlocked:
                have = {(r["kind"], r["item"]) for r in con.execute("SELECT kind, item FROM unlocks WHERE steamid = ?", (sid,))}
                stars = con.execute("SELECT COALESCE( SUM( value ), 0 ) AS s FROM server_stats WHERE steamid = ? AND key = 'stars'", (sid,)).fetchone()["s"]
                played = con.execute("SELECT COALESCE( SUM( value ), 0 ) AS s FROM server_stats WHERE steamid = ? AND key = 'playtime'", (sid,)).fetchone()["s"]
                allowed = UNLOCK_FREE + int(played // 60 // UNLOCK_MINUTES)
                today = con.execute("SELECT kind, COUNT(*) AS n FROM unlocks WHERE steamid = ? AND ts >= ? GROUP BY kind", (sid, day * 86400)).fetchall()
                new_today = {r["kind"]: r["n"] for r in today}
                spells = {i for k, i in have if k == "spell"} | {s for s in (unlocked.get("spell") or []) if isinstance(s, str)}
                for kind in UNLOCK_KINDS:
                    ids = unlocked.get(kind)
                    if not isinstance(ids, list):
                        continue
                    # lower tiers first, so a spell of the tier below counts within the same report
                    ids = [i for i in ids[:MAX_UNLOCKS] if isinstance(i, str) and ITEM_RE.match(i)]
                    if kind == "spell":
                        ids.sort(key=lambda i: SPELL_TIER.get(i, 99))
                    for item in ids:
                        if (kind, item) in have:
                            continue
                        why = None
                        if kind == "vanity":
                            if item in VANITY_BITS:
                                if stars < math.ceil(VANITY_BITS[item] * MIN_SCALE):
                                    why = "unlock_stars"
                            elif item not in VANITY_QUEST:
                                why = "unlock_unknown"
                        else:
                            tier = SPELL_TIER.get(item)
                            if not tier:
                                why = "unlock_unknown"
                            elif tier > 2 and not any(SPELL_TIER.get(s) == tier - 1 for s in spells):
                                why = "unlock_tier"
                        if not why and new_today.get(kind, 0) >= NEW_UNLOCKS_PER_DAY[kind]:
                            why = "unlock_daily"
                        if not why and len(have) >= allowed:
                            why = "unlock_playtime"
                        if why:
                            flag(con, now, server, sid, why + ":" + item, 0, 1, 0)
                            clamped += 1
                            continue
                        con.execute("INSERT OR IGNORE INTO unlocks ( steamid, kind, item, server, ts ) VALUES ( ?, ?, ?, ?, ? )", (sid, kind, item, server, now))
                        have.add((kind, item))
                        new_today[kind] = new_today.get(kind, 0) + 1
            chars = p.get("characters") if isinstance(p.get("characters"), list) else []
            for c in chars[:16]:
                if KEY_RE.match(str(c)):
                    con.execute("INSERT OR IGNORE INTO characters ( steamid, character ) VALUES ( ?, ? )", (sid, str(c)))
            accepted += 1
    return {"ok": True, "server": server, "players": accepted, "clamped": clamped}


def unlocks_of(con, steamids):
    out = {sid: {k: [] for k in UNLOCK_KINDS} for sid in steamids}
    marks = ",".join("?" * len(steamids))
    for r in con.execute(f"SELECT steamid, kind, item FROM unlocks WHERE steamid IN ( {marks} ) ORDER BY ts", tuple(steamids)):
        if r["kind"] in UNLOCK_KINDS:
            out[r["steamid"]][r["kind"]].append(r["item"])
    return out


def lookup_ok(request, install):
    check_ip(request)
    install = (install or "").lower()
    if INSTALL_RE.match(install) and not rate_ok("look:" + install, LOOKUP_PER_MIN):
        raise HTTPException(429, "too many lookups")


@app.get("/api/v1/unlocks/{steamid}")
def unlocks(steamid: str, request: Request, x_install_id: str = Header(default="")):
    """What a player unlocked on any game server (read by the servers themselves)."""
    lookup_ok(request, x_install_id)
    if not SID_RE.match(steamid):
        raise HTTPException(400, "bad steamid")
    with db() as con:
        return {"steamid": steamid, **unlocks_of(con, [steamid])[steamid]}


@app.post("/api/v1/unlocks")
async def unlocks_batch(request: Request, x_install_id: str = Header(default=""), x_nonce: str = Header(default=""),
                        x_sign: str = Header(default="")):
    """The same for a whole server at once: { "steamids": [ … up to 128 ] } -> { "players": [ { "steamid", "vanity", "spell" } ] }
    (a list, not keyed by SteamID: GMod's JSON reader turns numeric keys into lossy numbers)."""
    lookup_ok(request, x_install_id)
    raw = await request.body()
    check_signed(x_install_id.lower(), "unlocks", x_nonce, x_sign, raw)
    try:
        body = json.loads(raw)
    except Exception:
        raise HTTPException(400, "bad json")
    ids = body.get("steamids") if isinstance(body, dict) else None
    if not isinstance(ids, list):
        raise HTTPException(400, "steamids missing")
    ids = list(dict.fromkeys(str(s) for s in ids[:MAX_LOOKUP] if SID_RE.match(str(s))))
    if not ids:
        return {"players": []}
    with db() as con:
        found = unlocks_of(con, ids)
    return {"players": [{"steamid": sid, **found[sid]} for sid in ids]}


@app.get("/api/v1/leaderboard")
def leaderboard(stat: str = "stars", limit: int = 20):
    if not KEY_RE.match(stat):
        raise HTTPException(400, "bad stat")
    limit = max(1, min(limit, 100))
    fn = agg(stat)
    order = "ASC" if fn == "MIN" else "DESC"
    with db() as con:
        rows = con.execute(
            f"SELECT s.steamid, p.nick, {fn}( s.value ) AS value FROM server_stats s JOIN players p ON p.steamid = s.steamid "
            f"WHERE s.key = ? GROUP BY s.steamid ORDER BY value {order} LIMIT ?",
            (stat, limit),
        ).fetchall()
    return {"stat": stat, "aggregate": fn.lower(), "rows": [dict(r) for r in rows]}


@app.get("/api/v1/items/leaderboard")
def items_leaderboard(kind: str = "vanity", limit: int = 20):
    if kind not in UNLOCK_KINDS:
        raise HTTPException(400, "bad kind")
    with db() as con:
        rows = con.execute(
            "SELECT u.steamid, p.nick, COUNT(*) AS value FROM unlocks u JOIN players p ON p.steamid = u.steamid "
            "WHERE u.kind = ? GROUP BY u.steamid ORDER BY value DESC LIMIT ?",
            (kind, max(1, min(limit, 100))),
        ).fetchall()
    return {"kind": kind, "rows": [dict(r) for r in rows]}


@app.get("/api/v1/player/{steamid}")
def player(steamid: str):
    if not SID_RE.match(steamid):
        raise HTTPException(400, "bad steamid")
    with db() as con:
        p = con.execute("SELECT * FROM players WHERE steamid = ?", (steamid,)).fetchone()
        if not p:
            raise HTTPException(404, "unknown player")
        stats = {}
        for r in con.execute("SELECT key, value FROM server_stats WHERE steamid = ?", (steamid,)).fetchall():
            fn = agg(r["key"])
            cur = stats.get(r["key"])
            v = r["value"]
            stats[r["key"]] = v if cur is None else (cur + v if fn == "SUM" else (max(cur, v) if fn == "MAX" else min(cur, v)))
        items = {r["kind"]: r["c"] for r in con.execute("SELECT kind, COUNT(*) AS c FROM unlocks WHERE steamid = ? GROUP BY kind", (steamid,))}
        chars = [r["character"] for r in con.execute("SELECT character FROM characters WHERE steamid = ?", (steamid,))]
    return {"steamid": steamid, "nick": p["nick"], "last_seen": p["last_seen"], "stats": stats, "items": items, "characters": chars}


@app.get("/api/v1/history/{steamid}")
def player_history(steamid: str, days: int = 37):
    """The day's last total of stars / play time: { "series": { "stars": [ [ "YYYY-MM-DD", value ], … ] } }."""
    if not SID_RE.match(steamid):
        raise HTTPException(400, "bad steamid")
    days = max(1, min(days, HISTORY_MAX_DAYS))
    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
    series = {k: [] for k in HISTORY_KEYS}
    with db() as con:
        for r in con.execute("SELECT day, key, value FROM stats_daily WHERE steamid = ? AND day >= ? ORDER BY day", (steamid, since)):
            if r["key"] in series:
                series[r["key"]].append([r["day"], r["value"]])
        # the last value before the window, so the first days aren't empty
        for k in HISTORY_KEYS:
            prev = con.execute("SELECT day, value FROM stats_daily WHERE steamid = ? AND key = ? AND day < ? ORDER BY day DESC LIMIT 1",
                               (steamid, k, since)).fetchone()
            if prev:
                series[k].insert(0, [prev["day"], prev["value"]])
    return {"steamid": steamid, "series": series}


@app.get("/api/v1/stats/keys")
def stat_keys():
    with db() as con:
        return {"keys": [r["key"] for r in con.execute("SELECT DISTINCT key FROM server_stats ORDER BY key")]}


# ---------------------------------------------------------------------------------------------- admin
@app.get("/api/openapi.json", include_in_schema=False)
def openapi_json(request: Request):
    if not auth.is_admin(request):
        raise HTTPException(403, "admin only")
    return JSONResponse(app.openapi())


@app.get("/api/docs", include_in_schema=False)
def api_docs(request: Request):
    if not auth.is_admin(request):
        raise HTTPException(403, "admin only: log in with an admin Steam account")
    return get_swagger_ui_html(openapi_url="/api/openapi.json", title="MLP Maretime Bay API")


def admin(token, request: Request = None):
    # an admin's Steam login (MLP_ADMIN_STEAMIDS), or the old token for scripts (MLP_ADMIN_TOKEN, optional)
    if request is not None and auth.is_admin(request):
        return
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(403, "admin only")


@app.get("/api/v1/admin/flags")
def admin_flags(request: Request, limit: int = 100, x_admin_token: str = Header(default="")):
    admin(x_admin_token, request)
    with db() as con:
        rows = con.execute("SELECT * FROM flags ORDER BY ts DESC LIMIT ?", (max(1, min(limit, 1000)),)).fetchall()
    return {"rows": [dict(r) for r in rows]}


@app.delete("/api/v1/admin/player/{steamid}")
def admin_wipe_player(steamid: str, request: Request, x_admin_token: str = Header(default="")):
    """Forget a cheater: stats, items, unlocks, characters."""
    admin(x_admin_token, request)
    with lock, db() as con:
        for t in ("server_stats", "server_items", "unlocks", "characters", "budget", "players"):
            con.execute(f"DELETE FROM {t} WHERE steamid = ?", (steamid,))
    return {"ok": True}


@app.delete("/api/v1/admin/install/{install}")
def admin_wipe_install(install: str, request: Request, x_admin_token: str = Header(default="")):
    """Drop everything one install (or trusted server, "key:<name>") ever reported."""
    admin(x_admin_token, request)
    server = install if install.startswith("key:") else "i:" + install.lower()
    with lock, db() as con:
        for t in ("server_stats", "server_items", "unlocks"):
            con.execute(f"DELETE FROM {t} WHERE server = ?", (server,))
        con.execute("DELETE FROM servers WHERE name = ?", (server,))
    return {"ok": True}


def _page(name):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", name))


@app.get("/", include_in_schema=False)
def index():
    return _page("index.html")


@app.get("/me", include_in_schema=False)
def me_page():
    return _page("me.html")  # the login is checked by the API calls of the page


@app.get("/my-songs", include_in_schema=False)
def my_songs_page():
    return _page("my-songs.html")


@app.get("/editor/{u}", include_in_schema=False)
def editor_page(u: str):
    return _page("editor.html")


@app.get("/songs", include_in_schema=False)
def songs_page():
    return _page("songs.html")


@app.get("/songs/{u}", include_in_schema=False)
def song_page(u: str):
    return _page("songs.html")


@app.get("/admin", include_in_schema=False)
def admin_page():
    return _page("admin.html")  # the admin API checks the login


@app.exception_handler(500)
async def internal(_, exc):
    return JSONResponse({"ok": False, "error": "internal"}, status_code=500)
