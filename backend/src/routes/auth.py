from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlmodel import Session

from ..auth import (
    clear_auth_cookie,
    extract_auth_token,
    issue_token,
    require_user,
    revoke_token_value,
    set_auth_cookie,
    verify_password,
)
from ..db import get_session
from ..models import Credentials, UserOut
from ..tables import User

router = APIRouter()


@router.post("/login")
def login(credentials: Credentials, response: Response, session: Session = Depends(get_session)) -> dict:
    user = session.get(User, credentials.username)
    if not user or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    token = issue_token(session, credentials.username)
    set_auth_cookie(response, token)
    return {
        "user": UserOut(username=user.username, is_admin=user.is_admin, force_password_change=user.force_password_change),
    }


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(require_user)) -> UserOut:
    return UserOut(username=user.username, is_admin=user.is_admin, force_password_change=user.force_password_change)


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
