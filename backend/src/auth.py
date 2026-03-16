import hashlib
import secrets
from datetime import timedelta
from typing import Callable, Optional

from fastapi import Depends, Header, HTTPException, Request, Response, status
from passlib.hash import bcrypt
from sqlmodel import Session, select

from .config import settings
from .db import get_session
from .rbac import Permission, ensure_user_role_fields, has_permission
from .tables import ConnectToken, Token, User
from .time_utils import utc_now

_SESSION_TOKEN_DOMAIN = "session"
_CONNECT_TOKEN_DOMAIN = "connect"
_TOKEN_HASH_PREFIX = "sha256:"


def hash_password(password: str) -> str:
    return bcrypt.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.verify(password, hashed)
    except ValueError:
        return False


def _token_storage_key(token_value: str, *, domain: str) -> str:
    normalized = str(token_value or "").strip()
    if not normalized:
        return ""
    digest = hashlib.sha256(f"{domain}:{normalized}".encode("utf-8")).hexdigest()
    return f"{_TOKEN_HASH_PREFIX}{digest}"


def _is_token_storage_key(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized.startswith(_TOKEN_HASH_PREFIX) and len(normalized) == len(_TOKEN_HASH_PREFIX) + 64


def session_token_storage_key(token_value: str) -> str:
    return _token_storage_key(token_value, domain=_SESSION_TOKEN_DOMAIN)


def connect_token_storage_key(token_value: str) -> str:
    return _token_storage_key(token_value, domain=_CONNECT_TOKEN_DOMAIN)


def lookup_session_token(session: Session, token_value: str) -> Token | None:
    normalized = str(token_value or "").strip()
    if not normalized:
        return None
    hashed_key = session_token_storage_key(normalized)
    if hashed_key:
        token = session.get(Token, hashed_key)
        if token:
            return token
    if _is_token_storage_key(normalized):
        return None
    # Backward compatibility for legacy plaintext rows.
    return session.get(Token, normalized)


def _lookup_connect_token(session: Session, token_value: str) -> ConnectToken | None:
    normalized = str(token_value or "").strip()
    if not normalized:
        return None
    hashed_key = connect_token_storage_key(normalized)
    if hashed_key:
        token = session.get(ConnectToken, hashed_key)
        if token:
            return token
    if _is_token_storage_key(normalized):
        return None
    # Backward compatibility for legacy plaintext rows.
    return session.get(ConnectToken, normalized)


def issue_token(session: Session, username: str) -> str:
    token_value = secrets.token_urlsafe(48)
    session.add(Token(token=session_token_storage_key(token_value), username=username))
    session.commit()
    return token_value


def revoke_tokens(session: Session, username: str) -> None:
    tokens = session.exec(select(Token).where(Token.username == username)).all()
    for token in tokens:
        session.delete(token)
    session.commit()


def auth_cookie_name() -> str:
    return str(settings.auth_cookie_name or "blabs_session")


def _samesite_value(value: str, default: str = "lax") -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in {"lax", "strict", "none"}:
        normalized = default
    return normalized


def set_auth_cookie(response: Response, token_value: str) -> None:
    ttl_seconds = max(60, int(settings.auth_cookie_ttl_seconds or 86400))
    response.set_cookie(
        key=auth_cookie_name(),
        value=token_value,
        max_age=ttl_seconds,
        httponly=True,
        samesite=_samesite_value(settings.auth_cookie_samesite, "lax"),
        secure=bool(settings.auth_cookie_secure),
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        key=auth_cookie_name(),
        path="/",
        httponly=True,
        samesite=_samesite_value(settings.auth_cookie_samesite, "lax"),
        secure=bool(settings.auth_cookie_secure),
    )


def extract_auth_token(authorization: Optional[str], request: Request) -> str:
    prefix = "Bearer "
    auth_header = str(authorization or "").strip()
    if auth_header:
        if auth_header.startswith(prefix):
            token = auth_header[len(prefix) :].strip()
        else:
            token = auth_header
        if token:
            return token
    cookie_token = str(request.cookies.get(auth_cookie_name()) or "").strip()
    if cookie_token:
        return cookie_token
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing authorization token")


def revoke_token_value(session: Session, token_value: str) -> None:
    normalized = str(token_value or "").strip()
    if not normalized:
        return
    deleted = False
    hashed_key = session_token_storage_key(normalized)
    if hashed_key:
        token = session.get(Token, hashed_key)
        if token:
            session.delete(token)
            deleted = True
    if normalized != hashed_key and not _is_token_storage_key(normalized):
        legacy_token = session.get(Token, normalized)
        if legacy_token:
            session.delete(legacy_token)
            deleted = True
    if deleted:
        session.commit()


def _is_auth_token_expired(token: Token) -> bool:
    ttl_seconds = max(60, int(settings.auth_cookie_ttl_seconds or 86400))
    issued_at = token.issued_at or utc_now()
    return issued_at + timedelta(seconds=ttl_seconds) <= utc_now()


def issue_connect_token(
    session: Session,
    *,
    username: str,
    instance_id: str,
    resource_type: str = "container",
    token_type: str = "grant",
    ttl_seconds: int = 120,
) -> str:
    token_value = secrets.token_urlsafe(48)
    now = utc_now()
    row = ConnectToken(
        token=connect_token_storage_key(token_value),
        username=username,
        instance_id=instance_id,
        resource_type=resource_type,
        token_type=token_type,
        issued_at=now,
        expires_at=now + timedelta(seconds=max(15, int(ttl_seconds))),
    )
    session.add(row)
    session.commit()
    return token_value


def consume_connect_grant(
    session: Session,
    *,
    token_value: str,
    instance_id: str,
    resource_type: str = "container",
) -> User:
    row = _lookup_connect_token(session, token_value)
    now = utc_now()
    if (
        not row
        or row.token_type != "grant"
        or row.resource_type != resource_type
        or row.instance_id != instance_id
        or row.used_at is not None
        or row.expires_at <= now
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid connect token")
    user = session.get(User, row.username)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid connect token")
    row.used_at = now
    session.add(row)
    session.commit()
    return user


def validate_connect_session(
    session: Session,
    *,
    token_value: str,
    instance_id: str,
    resource_type: str = "container",
) -> User:
    row = _lookup_connect_token(session, token_value)
    now = utc_now()
    if (
        not row
        or row.token_type != "session"
        or row.resource_type != resource_type
        or row.instance_id != instance_id
        or row.expires_at <= now
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid connect session")
    user = session.get(User, row.username)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid connect session")
    return user


def require_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
) -> User:
    token_value = extract_auth_token(authorization, request)
    token = lookup_session_token(session, token_value)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    if _is_auth_token_expired(token):
        session.delete(token)
        session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session expired")
    user = session.get(User, token.username)
    if not user:
        session.delete(token)
        session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    if ensure_user_role_fields(user):
        session.add(user)
        session.commit()
        session.refresh(user)
    return user


def require_admin(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
) -> User:
    user = require_user(request=request, authorization=authorization, session=session)
    if not has_permission(user, Permission.ADMIN_ACCESS):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin required")
    return user


def require_permission(permission: str) -> Callable[[User], User]:
    def _require_permission(user: User = Depends(require_user)) -> User:
        if has_permission(user, permission):
            return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"missing permission: {permission}",
        )

    return _require_permission
