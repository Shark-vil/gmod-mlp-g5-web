"""Steam login (OpenID 2.0) and sessions for the website (PLANS.md 13.1 in the mod).

- GET  /auth/steam/login?next=/songs   -> to Steam (the password is typed there, never here)
- GET  /auth/steam/callback            <- Steam sends the player back; the answer is checked with Steam itself
                                          (check_authentication), only then the SteamID64 is trusted
- GET  /api/v1/me                      -> who is logged in (steamid, nick, avatar, admin) or 401
- POST /auth/logout
The site learns only the SteamID64 from Steam; the nick and the avatar come from the public profile
(steamcommunity.com/profiles/<id>?xml=1, no API key). Sessions are a signed cookie (HMAC, 30 days).
Admins: MLP_ADMIN_STEAMIDS (SteamID64s, comma separated) - the admin pages and /api/docs.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

STEAM_LOGIN = "https://steamcommunity.com/openid/login"
CLAIMED_RE = re.compile(r"^https://steamcommunity\.com/openid/id/(\d{17})$")
SESSION_COOKIE = "mlp_session"
SESSION_DAYS = 30
PUBLIC_URL = os.environ.get("MLP_PUBLIC_URL", "").rstrip("/")  # e.g. https://gmod-mlp-g5.itpony.pro
ADMIN_STEAMIDS = {s.strip() for s in os.environ.get("MLP_ADMIN_STEAMIDS", "").split(",") if re.fullmatch(r"\d{17}", s.strip())}

router = APIRouter()
_db = None  # set by init(): the main module's connection factory


def init(db_factory):
    global _db
    _db = db_factory
    with _db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS users ( steamid TEXT PRIMARY KEY, nick TEXT, avatar TEXT, first_login INTEGER,
                last_login INTEGER, banned INTEGER DEFAULT 0, ban_reason TEXT );
            CREATE TABLE IF NOT EXISTS meta ( key TEXT PRIMARY KEY, value TEXT );
            """
        )


def _secret():
    """MLP_SESSION_SECRET, or one made once and kept in the database (sessions survive restarts)."""
    env = os.environ.get("MLP_SESSION_SECRET", "")
    if env:
        return env.encode()
    with _db() as con:
        row = con.execute("SELECT value FROM meta WHERE key = 'session_secret'").fetchone()
        if row:
            return row["value"].encode()
        value = secrets.token_hex(32)
        con.execute("INSERT OR IGNORE INTO meta ( key, value ) VALUES ( 'session_secret', ? )", (value,))
        return con.execute("SELECT value FROM meta WHERE key = 'session_secret'").fetchone()["value"].encode()


def _sign(payload: bytes) -> str:
    return hmac.new(_secret(), payload, hashlib.sha256).hexdigest()


def make_cookie(steamid: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"sid": steamid, "exp": int(time.time()) + SESSION_DAYS * 86400}).encode()).decode()
    return body + "." + _sign(body.encode())


def read_cookie(value: str):
    try:
        body, sig = value.rsplit(".", 1)
        if not hmac.compare_digest(sig, _sign(body.encode())):
            return None
        data = json.loads(base64.urlsafe_b64decode(body.encode()))
        if data.get("exp", 0) < time.time() or not re.fullmatch(r"\d{17}", str(data.get("sid", ""))):
            return None
        return data["sid"]
    except Exception:
        return None


def current_user(request: Request):
    """The logged-in player as a dict (steamid, nick, avatar, admin, banned), or None."""
    sid = read_cookie(request.cookies.get(SESSION_COOKIE, ""))
    if not sid:
        return None
    with _db() as con:
        row = con.execute("SELECT * FROM users WHERE steamid = ?", (sid,)).fetchone()
    if not row:
        return None
    return {"steamid": sid, "nick": row["nick"], "avatar": row["avatar"], "admin": sid in ADMIN_STEAMIDS,
            "banned": bool(row["banned"]), "profile": f"https://steamcommunity.com/profiles/{sid}"}


def require_user(request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401, "login required")
    if u["banned"]:
        raise HTTPException(403, "banned")
    return u


def is_admin(request: Request) -> bool:
    u = current_user(request)
    return bool(u and u["admin"])


def _base(request: Request) -> str:
    if PUBLIC_URL:
        return PUBLIC_URL
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def _safe_next(value: str) -> str:
    # only a path on this site (no "//other.site", no scheme)
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value[:300]


@router.get("/auth/steam/login")
def steam_login(request: Request, next: str = "/"):
    base = _base(request)
    params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": f"{base}/auth/steam/callback?" + urllib.parse.urlencode({"next": _safe_next(next)}),
        "openid.realm": base,
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }
    return RedirectResponse(STEAM_LOGIN + "?" + urllib.parse.urlencode(params), status_code=302)


def verify_with_steam(params: dict) -> bool:
    """Ask Steam whether this answer is really its own (OpenID check_authentication)."""
    data = dict(params)
    data["openid.mode"] = "check_authentication"
    req = urllib.request.Request(STEAM_LOGIN, data=urllib.parse.urlencode(data).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "mlp-g5-web"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            text = r.read(4096).decode("utf-8", "replace")
    except Exception:
        return False
    return any(line.strip() == "is_valid:true" for line in text.splitlines())


def fetch_profile(steamid: str):
    """The public nick and avatar (steamcommunity.com XML profile, no key)."""
    try:
        req = urllib.request.Request(f"https://steamcommunity.com/profiles/{steamid}?xml=1", headers={"User-Agent": "mlp-g5-web"})
        with urllib.request.urlopen(req, timeout=10) as r:
            root = ET.fromstring(r.read(200000))
        nick = (root.findtext("steamID") or "").strip()[:64]
        avatar = (root.findtext("avatarMedium") or "").strip()
        if not avatar.startswith("https://"):
            avatar = ""
        return nick or None, avatar[:300] or None
    except Exception:
        return None, None


@router.get("/auth/steam/callback")
def steam_callback(request: Request, next: str = "/"):
    q = dict(request.query_params)
    openid = {k: v for k, v in q.items() if k.startswith("openid.")}
    base = _base(request)
    # Steam's answer, for this site, then confirmed by Steam itself
    claimed = CLAIMED_RE.match(openid.get("openid.claimed_id", ""))
    ok = (
        openid.get("openid.mode") == "id_res"
        and openid.get("openid.op_endpoint") == STEAM_LOGIN
        and claimed is not None
        and openid.get("openid.identity") == openid.get("openid.claimed_id")
        and openid.get("openid.return_to", "").startswith(f"{base}/auth/steam/callback")
        and verify_with_steam(openid)
    )
    if not ok:
        return RedirectResponse("/?login=failed", status_code=302)
    sid = claimed.group(1)
    nick, avatar = fetch_profile(sid)
    now = int(time.time())
    with _db() as con:
        con.execute(
            "INSERT INTO users ( steamid, nick, avatar, first_login, last_login ) VALUES ( ?, ?, ?, ?, ? ) "
            "ON CONFLICT ( steamid ) DO UPDATE SET nick = COALESCE( excluded.nick, users.nick ), "
            "avatar = COALESCE( excluded.avatar, users.avatar ), last_login = excluded.last_login",
            (sid, nick or sid, avatar, now, now),
        )
    resp = RedirectResponse(_safe_next(next), status_code=302)
    resp.set_cookie(SESSION_COOKIE, make_cookie(sid), max_age=SESSION_DAYS * 86400, httponly=True,
                    secure=base.startswith("https://"), samesite="lax", path="/")
    return resp


@router.get("/api/v1/me")
def me(request: Request):
    u = current_user(request)
    if not u:
        return JSONResponse({"logged_in": False}, status_code=401)
    return {"logged_in": True, **u}


@router.post("/auth/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp
