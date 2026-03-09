import base64
import hashlib
import re
import secrets
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
import requests
from sqlmodel import Session, select

from ..auth import (
    clear_auth_cookie,
    extract_auth_token,
    hash_password,
    issue_token,
    require_user,
    revoke_token_value,
    set_auth_cookie,
    verify_password,
)
from ..db import get_session
from ..models import Credentials, UserOut
from ..rbac import Role, can_access_admin, list_permissions_for_role, role_for_user
from ..services.team_quotas import normalize_team
from ..tables import Config, OIDCLoginState, User
from ..time_utils import utc_now

router = APIRouter()
OIDC_STATE_TTL_SECONDS = 300
OIDC_HTTP_TIMEOUT_SECONDS = 15
OIDC_SCOPE = "openid profile email"
_USERNAME_CLEAN_RE = re.compile(r"[^a-zA-Z0-9._@-]+")


def _user_out(user: User) -> UserOut:
    role = role_for_user(user)
    return UserOut(
        username=user.username,
        role=role,
        team=normalize_team(getattr(user, "team", None)),
        is_admin=can_access_admin(role),
        force_password_change=user.force_password_change,
        permissions=list_permissions_for_role(role),
        can_access_admin=can_access_admin(role),
    )


def _sso_config(session: Session) -> Config:
    cfg = session.get(Config, 1) or Config(id=1)
    session.add(cfg)
    session.commit()
    return cfg


def _clean_expired_oidc_states(session: Session) -> None:
    now = utc_now()
    expired = session.exec(select(OIDCLoginState).where(OIDCLoginState.expires_at <= now)).all()
    if not expired:
        return
    for row in expired:
        session.delete(row)
    session.commit()


def _require_oidc_config(cfg: Config) -> None:
    if not cfg.sso_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="SSO is disabled")
    required = {
        "sso_client_id": cfg.sso_client_id,
        "sso_authorize_url": cfg.sso_authorize_url,
        "sso_token_url": cfg.sso_token_url,
        "sso_userinfo_url": cfg.sso_userinfo_url,
        "sso_redirect_url": cfg.sso_redirect_url,
    }
    missing = [name for name, value in required.items() if not str(value or "").strip()]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"SSO config missing required fields: {', '.join(missing)}",
        )


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _pkce_code_challenge(verifier: str) -> str:
    return _base64url(hashlib.sha256(verifier.encode("ascii")).digest())


def _append_query(url: str, params: dict[str, str]) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({k: v for k, v in params.items() if str(v or "").strip()})
    return urlunparse(parsed._replace(query=urlencode(query)))


def _sanitize_return_to(return_to: str | None, request: Request) -> str:
    raw = str(return_to or "").strip()
    request_origin = str(request.headers.get("origin") or "").strip()
    default_origin = f"{request.url.scheme}://{request.url.netloc}"
    parsed_origin = urlparse(request_origin) if request_origin else None
    allowed_hosts = {request.url.hostname}
    if parsed_origin and parsed_origin.hostname:
        allowed_hosts.add(parsed_origin.hostname)
    if not raw:
        raw = "/"
    if raw.startswith("/"):
        base = request_origin if parsed_origin and parsed_origin.hostname in allowed_hosts else default_origin
        return f"{base.rstrip('/')}{raw}"
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"} and parsed.hostname in allowed_hosts:
        return urlunparse(parsed._replace(fragment=""))
    return f"{default_origin}/"


def _oidc_username_from_claims(claims: dict) -> str:
    raw_candidates = [
        claims.get("preferred_username"),
        claims.get("email"),
        claims.get("upn"),
        claims.get("sub"),
        claims.get("name"),
    ]
    source = ""
    for value in raw_candidates:
        text = str(value or "").strip()
        if text:
            source = text
            break
    if not source:
        source = f"user-{secrets.token_hex(4)}"
    normalized = _USERNAME_CLEAN_RE.sub("-", source.lower()).strip("-._@")
    if len(normalized) > 64:
        digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:10]
        normalized = f"{normalized[:53]}-{digest}"
    if len(normalized) < 3:
        normalized = f"user-{secrets.token_hex(4)}"
    return normalized[:64]


def _oidc_exchange_token(cfg: Config, code: str, code_verifier: str) -> dict:
    payload = {
        "grant_type": "authorization_code",
        "client_id": str(cfg.sso_client_id),
        "code": code,
        "redirect_uri": str(cfg.sso_redirect_url),
        "code_verifier": code_verifier,
    }
    client_secret = str(cfg.sso_client_secret or "").strip()
    if client_secret:
        payload["client_secret"] = client_secret
    try:
        resp = requests.post(
            str(cfg.sso_token_url),
            data=payload,
            headers={"Accept": "application/json"},
            timeout=OIDC_HTTP_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"OIDC token request failed: {exc}") from exc
    if resp.status_code >= 400:
        detail = (resp.text or "").strip()[:300]
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"OIDC token exchange failed: {detail}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="OIDC token response was not JSON") from exc
    if not str(body.get("access_token") or "").strip():
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="OIDC token response missing access_token")
    return body


def _oidc_userinfo(cfg: Config, access_token: str) -> dict:
    try:
        resp = requests.get(
            str(cfg.sso_userinfo_url),
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=OIDC_HTTP_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"OIDC userinfo request failed: {exc}") from exc
    if resp.status_code >= 400:
        detail = (resp.text or "").strip()[:300]
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"OIDC userinfo failed: {detail}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="OIDC userinfo response was not JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="OIDC userinfo payload invalid")
    return body


def _redirect_with_auth_error(target: str, message: str) -> RedirectResponse:
    url = _append_query(target, {"auth_error": message[:200]})
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@router.post("/login")
def login(credentials: Credentials, response: Response, session: Session = Depends(get_session)) -> dict:
    user = session.get(User, credentials.username)
    if not user or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    token = issue_token(session, credentials.username)
    set_auth_cookie(response, token)
    return {"user": _user_out(user)}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(require_user)) -> UserOut:
    return _user_out(user)


@router.get("/sso/start")
def sso_start(
    request: Request,
    return_to: str | None = Query(default="/"),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    cfg = _sso_config(session)
    _require_oidc_config(cfg)
    _clean_expired_oidc_states(session)

    now = utc_now()
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(64)
    callback_return_to = _sanitize_return_to(return_to, request)
    session.add(
        OIDCLoginState(
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
            return_to=callback_return_to,
            created_at=now,
            expires_at=now + timedelta(seconds=OIDC_STATE_TTL_SECONDS),
        )
    )
    session.commit()

    authorize_url = _append_query(
        str(cfg.sso_authorize_url),
        {
            "response_type": "code",
            "client_id": str(cfg.sso_client_id),
            "redirect_uri": str(cfg.sso_redirect_url),
            "scope": OIDC_SCOPE,
            "state": state,
            "nonce": nonce,
            "code_challenge": _pkce_code_challenge(code_verifier),
            "code_challenge_method": "S256",
        },
    )
    return {"authorize_url": authorize_url}


@router.get("/sso/callback")
def sso_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    cfg = _sso_config(session)
    _require_oidc_config(cfg)
    _clean_expired_oidc_states(session)

    state_value = str(state or "").strip()
    if not state_value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing oidc state")
    state_row = session.get(OIDCLoginState, state_value)
    if not state_row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid oidc state")
    if state_row.expires_at <= utc_now():
        session.delete(state_row)
        session.commit()
        return _redirect_with_auth_error(state_row.return_to, "oidc_state_expired")

    return_to = state_row.return_to
    code_verifier = state_row.code_verifier
    session.delete(state_row)
    session.commit()

    if error:
        detail = str(error_description or error or "oidc_error").strip() or "oidc_error"
        return _redirect_with_auth_error(return_to, detail)
    if not str(code or "").strip():
        return _redirect_with_auth_error(return_to, "missing_oidc_code")

    token_payload = _oidc_exchange_token(cfg, str(code), code_verifier)
    claims = _oidc_userinfo(cfg, str(token_payload.get("access_token")))
    username = _oidc_username_from_claims(claims)
    user = session.get(User, username)
    if not user:
        user = User(
            username=username,
            password_hash=hash_password(secrets.token_urlsafe(32)),
            role=Role.USER,
            team=normalize_team(None),
            is_admin=False,
            force_password_change=False,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
    token = issue_token(session, user.username)
    response = RedirectResponse(url=return_to or _sanitize_return_to("/", request), status_code=status.HTTP_302_FOUND)
    set_auth_cookie(response, token)
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> Response:
    try:
        token_value = extract_auth_token(authorization, request)
    except HTTPException:
        token_value = ""
    if token_value:
        revoke_token_value(session, token_value)
    clear_auth_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
