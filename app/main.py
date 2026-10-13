"""MLP: Maretime Bay — optional stats service for the GMod mod.

Game servers POST snapshots of their players' saved data (their own SQLite stays the source of truth);
this service keeps one copy per (game server, player) and serves aggregated leaderboards.

Aggregation across game servers: counters are summed, "best_*" keys take the maximum, "time_*" keys
the minimum. Anti-cheat is deliberately light: a server key, a rate limit and a plausibility check on
how fast counters may grow (implausible jumps are clamped and logged in the `flags` table).
"""
import json
import math
import os
import re
import sqlite3
import threading
import time
from collections import defaultdict, deque

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

DB_PATH = os.environ.get("MLP_DB", "/data/stats.db")
# "server-name:secret-key,other:key2"; empty + MLP_OPEN=1 accepts any key (local testing only)
SERVER_KEYS = {}
for pair in filter(None, (p.strip() for p in os.environ.get("MLP_SERVER_KEYS", "").split(","))):
    name, _, key = pair.partition(":")
    if key:
        SERVER_KEYS[key] = name
OPEN = os.environ.get("MLP_OPEN", "0") == "1"
RATE_PER_MIN = int(os.environ.get("MLP_RATE_PER_MIN", "30"))

MAX_PLAYERS = 128
MAX_STATS = 96
KEY_RE = re.compile(r"^[a-z0-9_]{1,40}$")
SID_RE = re.compile(r"^\d{1,20}$")
ITEM_RE = re.compile(r"^[A-Za-z0-9_]{1,48}$")
UNLOCK_KINDS = ("vanity", "spell")
MAX_UNLOCKS = 128
# fastest believable growth per minute since the last report (plus a small allowance)
GROWTH = {"stars": (150, 300), "vanity": (10, 40), "playtime": (70, 600)}
GROWTH_DEFAULT = (20, 50)

app = FastAPI(title="MLP Maretime Bay stats", docs_url="/api/docs", openapi_url="/api/openapi.json")
lock = threading.Lock()
hits = defaultdict(deque)


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
            CREATE TABLE IF NOT EXISTS players ( steamid TEXT PRIMARY KEY, nick TEXT, first_seen INTEGER, last_seen INTEGER, last_server TEXT );
            CREATE TABLE IF NOT EXISTS server_stats ( server TEXT, steamid TEXT, key TEXT, value REAL, updated INTEGER,
                PRIMARY KEY ( server, steamid, key ) );
            CREATE TABLE IF NOT EXISTS server_items ( server TEXT, steamid TEXT, kind TEXT, count INTEGER, PRIMARY KEY ( server, steamid, kind ) );
            CREATE TABLE IF NOT EXISTS unlocks ( steamid TEXT, kind TEXT, item TEXT, server TEXT, ts INTEGER, PRIMARY KEY ( steamid, kind, item ) );
            CREATE TABLE IF NOT EXISTS characters ( steamid TEXT, character TEXT, PRIMARY KEY ( steamid, character ) );
            CREATE TABLE IF NOT EXISTS flags ( ts INTEGER, server TEXT, steamid TEXT, key TEXT, old REAL, new REAL, kept REAL );
            CREATE INDEX IF NOT EXISTS idx_stats_key ON server_stats ( key );
            """
        )


init()


def agg(key):
    if key.startswith("best_"):
        return "MAX"
    if key.startswith("time_"):
        return "MIN"
    return "SUM"


def rate_ok(key):
    now = time.time()
    q = hits[key]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_PER_MIN:
        return False
    q.append(now)
    return True


def num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return max(0.0, min(float(v), 1e9))


@app.get("/health")
def health():
    return {"ok": True, "time": int(time.time())}


@app.post("/api/v1/report")
async def report(request: Request, x_server_key: str = Header(default="")):
    if x_server_key in SERVER_KEYS:
        server = SERVER_KEYS[x_server_key]
    elif OPEN and x_server_key:
        server = None  # named by the report itself
    else:
        raise HTTPException(401, "unknown server key")
    if not rate_ok(x_server_key):
        raise HTTPException(429, "too many reports")
    try:
        body = json.loads(await request.body())
    except Exception:
        raise HTTPException(400, "bad json")
    if not isinstance(body, dict) or not isinstance(body.get("players"), list):
        raise HTTPException(400, "players missing")
    info = body.get("server") if isinstance(body.get("server"), dict) else {}
    if server is None:
        server = str(info.get("name") or "unnamed")[:64]
    mapname = str(info.get("map") or "")[:64]
    now = int(time.time())
    accepted, clamped = 0, 0

    with lock, db() as con:
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
            stats = dict(p.get("stats") or {}) if isinstance(p.get("stats"), dict) else {}
            pt = num(p.get("playtime"))
            if pt is not None:
                stats["playtime"] = pt
            for key, value in list(stats.items())[:MAX_STATS]:
                key, value = str(key), num(value)
                if value is None or not KEY_RE.match(key):
                    continue
                row = con.execute("SELECT value, updated FROM server_stats WHERE server = ? AND steamid = ? AND key = ?", (server, sid, key)).fetchone()
                kept = value
                if row and agg(key) == "SUM" and value > row["value"]:
                    per_min, allowance = GROWTH.get(key, GROWTH_DEFAULT)
                    minutes = max(0.0, (now - row["updated"]) / 60)
                    limit = row["value"] + allowance + per_min * minutes
                    if value > limit:
                        kept = limit
                        clamped += 1
                        con.execute("INSERT INTO flags VALUES ( ?, ?, ?, ?, ?, ?, ? )", (now, server, sid, key, row["value"], value, kept))
                con.execute("REPLACE INTO server_stats ( server, steamid, key, value, updated ) VALUES ( ?, ?, ?, ?, ? )", (server, sid, key, kept, now))
            items = p.get("items") if isinstance(p.get("items"), dict) else {}
            for kind, count in list(items.items())[:16]:
                count = num(count)
                if count is not None and KEY_RE.match(str(kind)):
                    con.execute("REPLACE INTO server_items ( server, steamid, kind, count ) VALUES ( ?, ?, ?, ? )", (server, sid, str(kind), int(count)))
            # unlocked collectibles / spells: kept forever, shared with every server of this service
            unlocked = p.get("unlocked") if isinstance(p.get("unlocked"), dict) else {}
            for kind in UNLOCK_KINDS:
                ids = unlocked.get(kind)
                if not isinstance(ids, list):
                    continue
                for item in ids[:MAX_UNLOCKS]:
                    if isinstance(item, str) and ITEM_RE.match(item):
                        con.execute("INSERT OR IGNORE INTO unlocks ( steamid, kind, item, server, ts ) VALUES ( ?, ?, ?, ?, ? )", (sid, kind, item, server, now))
            chars = p.get("characters") if isinstance(p.get("characters"), list) else []
            for c in chars[:16]:
                if KEY_RE.match(str(c)):
                    con.execute("INSERT OR IGNORE INTO characters ( steamid, character ) VALUES ( ?, ? )", (sid, str(c)))
            accepted += 1
    return {"ok": True, "server": server, "players": accepted, "clamped": clamped}


def server_of(key):
    if key in SERVER_KEYS:
        return SERVER_KEYS[key]
    if OPEN and key:
        return "open"
    raise HTTPException(401, "unknown server key")


@app.get("/api/v1/unlocks/{steamid}")
def unlocks(steamid: str, x_server_key: str = Header(default="")):
    """What a player unlocked on any game server of this service (read by the servers themselves)."""
    server_of(x_server_key)
    if not SID_RE.match(steamid):
        raise HTTPException(400, "bad steamid")
    out = {k: [] for k in UNLOCK_KINDS}
    with db() as con:
        for r in con.execute("SELECT kind, item FROM unlocks WHERE steamid = ? ORDER BY ts", (steamid,)):
            if r["kind"] in out:
                out[r["kind"]].append(r["item"])
    return {"steamid": steamid, **out}


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
    if not KEY_RE.match(kind):
        raise HTTPException(400, "bad kind")
    with db() as con:
        rows = con.execute(
            "SELECT i.steamid, p.nick, MAX( i.count ) AS value FROM server_items i JOIN players p ON p.steamid = i.steamid "
            "WHERE i.kind = ? GROUP BY i.steamid ORDER BY value DESC LIMIT ?",
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
        items = {r["kind"]: r["c"] for r in con.execute("SELECT kind, MAX( count ) AS c FROM server_items WHERE steamid = ? GROUP BY kind", (steamid,))}
        chars = [r["character"] for r in con.execute("SELECT character FROM characters WHERE steamid = ?", (steamid,))]
    return {"steamid": steamid, "nick": p["nick"], "last_seen": p["last_seen"], "stats": stats, "items": items, "characters": chars}


@app.get("/api/v1/stats/keys")
def stat_keys():
    with db() as con:
        return {"keys": [r["key"] for r in con.execute("SELECT DISTINCT key FROM server_stats ORDER BY key")]}


@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.exception_handler(500)
async def internal(_, exc):
    return JSONResponse({"ok": False, "error": "internal"}, status_code=500)
