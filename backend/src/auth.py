import secrets
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from passlib.hash import bcrypt
from sqlmodel import Session, select

from .db import get_session
from .tables import Token, User


def hash_password(password: str) -> str:
    return bcrypt.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.verify(password, hashed)
    except ValueError:
        return False


def issue_token(session: Session, username: str) -> str:
    token_value = secrets.token_hex(32)
    session.add(Token(token=token_value, username=username))
    session.commit()
    return token_value


def revoke_tokens(session: Session, username: str) -> None:
    tokens = session.exec(select(Token).where(Token.username == username)).all()
    for token in tokens:
        session.delete(token)
    session.commit()


def _extract_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing authorization header")
    prefix = "Bearer "
    if authorization.startswith(prefix):
        return authorization[len(prefix) :].strip()
    return authorization.strip()


def require_user(
    authorization: Optional[str] = Header(default=None), session: Session = Depends(get_session)
) -> User:
    token_value = _extract_token(authorization)
    token = session.get(Token, token_value)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    user = session.get(User, token.username)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    return user


def require_admin(
    authorization: Optional[str] = Header(default=None), session: Session = Depends(get_session)
) -> User:
    user = require_user(authorization, session)
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin required")
    return user
