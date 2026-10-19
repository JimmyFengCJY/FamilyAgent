import hashlib
import hmac
import secrets
import time
from fastapi import HTTPException, Request, Response


COOKIE = "familyagent_session"


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000).hex()
    return f"{salt}${digest}"


def verify_password(password, saved):
    salt, digest = saved.split("$")
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000).hex()
    return hmac.compare_digest(candidate, digest)


def current_user(request: Request):
    store = request.app.state.store
    token = request.cookies.get(COOKIE, "")
    with store.connect() as conn:
        row = conn.execute("SELECT user_id FROM sessions WHERE token=? AND expires>?", (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        user = store.user(row[0], conn) if row else None
    if not user or (user["id"].startswith("demo-") and not request.app.state.demo_login):
        raise HTTPException(401, "请先登录")
    return user


def new_session(store, user_id, response: Response, secure=False):
    token = secrets.token_urlsafe(32)
    with store.tx() as conn:
        conn.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        conn.execute("INSERT INTO sessions VALUES(?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, time.time() + 86400 * 7))
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=secure, max_age=86400 * 7)


def logout(store, request, response):
    token = request.cookies.get(COOKIE, "")
    with store.tx() as conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie(COOKIE)
