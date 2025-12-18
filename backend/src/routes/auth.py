from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from ..auth import issue_token, verify_password
from ..db import get_session
from ..models import Credentials, UserOut
from ..tables import User

router = APIRouter()


@router.post("/login")
def login(credentials: Credentials, session: Session = Depends(get_session)) -> dict:
    user = session.get(User, credentials.username)
    if not user or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    token = issue_token(session, credentials.username)
    return {
        "token": token,
        "user": UserOut(username=user.username, is_admin=user.is_admin, force_password_change=user.force_password_change),
    }
