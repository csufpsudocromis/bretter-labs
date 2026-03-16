import base64
import hashlib
import logging
import re
import secrets
import threading
import time
from collections import deque
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
    lookup_session_token,
    require_user,
    revoke_token_value,
    set_auth_cookie,
    verify_password,
)
from ..config import settings
from ..db import get_session
from ..models import Credentials, UserOut
from ..rbac import Role, can_access_admin, list_permissions_for_role, role_for_user
from ..secret_codec import decrypt_secret
from ..services.ldap_auth import (
    LDAPRuntimeConfig,
    authenticate as ldap_authenticate,
    missing_required_fields as ldap_missing_required_fields,
)
from ..services.team_quotas import normalize_team
from ..tables import Config, OIDCLoginState, User
from ..time_utils import utc_now

router = APIRouter()
logger = logging.getLogger(__name__)
OIDC_STATE_TTL_SECONDS = 300
OIDC_HTTP_TIMEOUT_SECONDS = 15
OIDC_SCOPE = "openid profile email"
_USERNAME_CLEAN_RE = re.compile(r"[^a-zA-Z0-9._@-]+")
_LOGIN_ATTEMPTS: dict[str, deque[float]] = {}
_LOGIN_BLOCKED_UNTIL: dict[str, float] = {}
_LOGIN_ATTEMPT_LOCK = threading.Lock()


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


def _request_ip(request: Request) -> str:
    forwarded = str(request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        first = str(forwarded.split(",", 1)[0]).strip()
        if first:
            return first[:128]
    real_ip = str(request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip[:128]
    client_host = str(getattr(request.client, "host", "") or "").strip()
    return (client_host or "unknown")[:128]


def _rate_limit_key(request: Request, username: str) -> str:
    user_key = str(username or "").strip().lower() or "<empty>"
    return f"{_request_ip(request)}::{user_key}"


def _auth_rate_limit_values() -> tuple[int, int, int]:
    window_seconds = max(10, int(settings.auth_login_rate_limit_window_seconds or 300))
    max_attempts = max(1, int(settings.auth_login_rate_limit_max_attempts or 5))
    lockout_seconds = max(10, int(settings.auth_login_lockout_seconds or 300))
    return window_seconds, max_attempts, lockout_seconds


def _is_login_rate_limited(rate_key: str, now: float) -> int:
    window_seconds, _, _ = _auth_rate_limit_values()
    with _LOGIN_ATTEMPT_LOCK:
        blocked_until = float(_LOGIN_BLOCKED_UNTIL.get(rate_key, 0.0) or 0.0)
        if blocked_until > now:
            return max(1, int(blocked_until - now))
        if blocked_until:
            _LOGIN_BLOCKED_UNTIL.pop(rate_key, None)
        attempts = _LOGIN_ATTEMPTS.get(rate_key)
        if not attempts:
            return 0
        cutoff = now - window_seconds
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        if not attempts:
            _LOGIN_ATTEMPTS.pop(rate_key, None)
    return 0


def _record_login_failure(rate_key: str, now: float) -> int:
    window_seconds, max_attempts, lockout_seconds = _auth_rate_limit_values()
    with _LOGIN_ATTEMPT_LOCK:
        attempts = _LOGIN_ATTEMPTS.setdefault(rate_key, deque())
        cutoff = now - window_seconds
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        attempts.append(now)
        if len(attempts) >= max_attempts:
            blocked_until = now + lockout_seconds
            _LOGIN_BLOCKED_UNTIL[rate_key] = blocked_until
            attempts.clear()
            return lockout_seconds
    return 0


def _clear_login_failures(rate_key: str) -> None:
    with _LOGIN_ATTEMPT_LOCK:
        _LOGIN_ATTEMPTS.pop(rate_key, None)
        _LOGIN_BLOCKED_UNTIL.pop(rate_key, None)


def _audit_auth_event(
    *,
    event: str,
    outcome: str,
    request: Request,
    username: str,
    source: str,
    detail: str = "",
) -> None:
    logger.info(
        "auth_event event=%s outcome=%s source=%s user=%s ip=%s detail=%s",
        event,
        outcome,
        source,
        str(username or "").strip()[:128],
        _request_ip(request),
        str(detail or "").strip()[:256],
    )


def _decrypt_runtime_secret_or_raise(raw_value: str, *, field_name: str) -> str:
    try:
        return decrypt_secret(raw_value)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{field_name} is configured but could not be decrypted.",
        ) from exc


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
    client_secret = _decrypt_runtime_secret_or_raise(
        str(cfg.sso_client_secret or ""), field_name="sso_client_secret"
    ).strip()
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
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"OIDC token request failed: {exc}"
        ) from exc
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
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"OIDC userinfo request failed: {exc}"
        ) from exc
    if resp.status_code >= 400:
        detail = (resp.text or "").strip()[:300]
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"OIDC userinfo failed: {detail}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="OIDC userinfo response was not JSON"
        ) from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="OIDC userinfo payload invalid")
    return body


def _redirect_with_auth_error(target: str, message: str) -> RedirectResponse:
    url = _append_query(target, {"auth_error": message[:200]})
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@router.post("/login")
def login(
    credentials: Credentials, request: Request, response: Response, session: Session = Depends(get_session)
) -> dict:
    username = str(credentials.username or "").strip()
    password = str(credentials.password or "")
    rate_key = _rate_limit_key(request, username)
    now = time.time()
    retry_after = _is_login_rate_limited(rate_key, now)
    if retry_after > 0:
        _audit_auth_event(
            event="login",
            outcome="rate_limited",
            request=request,
            username=username,
            source="local",
            detail=f"retry_after={retry_after}s",
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many failed login attempts; try again later",
            headers={"Retry-After": str(retry_after)},
        )

    user = session.get(User, username)
    if user and verify_password(password, user.password_hash):
        _clear_login_failures(rate_key)
        token = issue_token(session, user.username)
        set_auth_cookie(response, token)
        _audit_auth_event(event="login", outcome="success", request=request, username=user.username, source="local")
        return {"user": _user_out(user)}

    cfg = session.get(Config, 1) or Config(id=1)
    session.add(cfg)
    session.commit()
    if not bool(cfg.ldap_enabled):
        lockout = _record_login_failure(rate_key, time.time())
        _audit_auth_event(
            event="login",
            outcome="failed",
            request=request,
            username=username,
            source="local",
            detail="invalid_credentials",
        )
        if lockout > 0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too many failed login attempts; try again later",
                headers={"Retry-After": str(lockout)},
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    ldap_cfg = LDAPRuntimeConfig(
        enabled=bool(cfg.ldap_enabled),
        server_uri=str(cfg.ldap_server_uri or "").strip(),
        bind_dn=str(cfg.ldap_bind_dn or "").strip(),
        bind_password=_decrypt_runtime_secret_or_raise(
            str(cfg.ldap_bind_password or ""), field_name="ldap_bind_password"
        ),
        user_base_dn=str(cfg.ldap_user_base_dn or "").strip(),
        user_filter=str(cfg.ldap_user_filter or "").strip() or "(uid={username})",
        start_tls=bool(cfg.ldap_start_tls),
        insecure_skip_verify=bool(cfg.ldap_insecure_skip_verify),
        timeout_seconds=max(3, min(60, int(cfg.ldap_timeout_seconds or 10))),
    )
    missing = ldap_missing_required_fields(ldap_cfg)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LDAP authentication is enabled but not fully configured: {', '.join(missing)}",
        )

    try:
        ldap_ok, _ = ldap_authenticate(username, password, ldap_cfg)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"LDAP configuration error: {exc}"
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"LDAP authentication backend error: {exc}"
        ) from exc
    if not ldap_ok:
        lockout = _record_login_failure(rate_key, time.time())
        _audit_auth_event(
            event="login",
            outcome="failed",
            request=request,
            username=username,
            source="ldap",
            detail="invalid_credentials",
        )
        if lockout > 0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too many failed login attempts; try again later",
                headers={"Retry-After": str(lockout)},
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    if not user:
        if not bool(cfg.ldap_auto_create_users):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="LDAP account is not provisioned. Contact an administrator.",
            )
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
    set_auth_cookie(response, token)
    _clear_login_failures(rate_key)
    _audit_auth_event(event="login", outcome="success", request=request, username=user.username, source="ldap")
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
        _audit_auth_event(
            event="sso_callback",
            outcome="failed",
            request=request,
            username="",
            source="oidc",
            detail="state_expired",
        )
        return _redirect_with_auth_error(state_row.return_to, "oidc_state_expired")

    return_to = state_row.return_to
    code_verifier = state_row.code_verifier
    session.delete(state_row)
    session.commit()

    if error:
        detail = str(error_description or error or "oidc_error").strip() or "oidc_error"
        _audit_auth_event(
            event="sso_callback",
            outcome="failed",
            request=request,
            username="",
            source="oidc",
            detail=detail,
        )
        return _redirect_with_auth_error(return_to, detail)
    if not str(code or "").strip():
        _audit_auth_event(
            event="sso_callback",
            outcome="failed",
            request=request,
            username="",
            source="oidc",
            detail="missing_oidc_code",
        )
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
    _audit_auth_event(event="sso_callback", outcome="success", request=request, username=user.username, source="oidc")
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> Response:
    username = ""
    token_value = ""
    try:
        token_value = extract_auth_token(authorization, request)
    except HTTPException:
        token_value = ""
    if token_value:
        token_row = lookup_session_token(session, token_value)
        if token_row:
            username = str(token_row.username or "")
        revoke_token_value(session, token_value)
    clear_auth_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    _audit_auth_event(event="logout", outcome="success", request=request, username=username, source="local")
    return response
