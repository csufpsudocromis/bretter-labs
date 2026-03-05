from sqlmodel import Session, select

from ..tables import User


def lock_user_launch_slot(session: Session, username: str) -> User | None:
    # Row-level lock serializes launch attempts for a user across backend replicas.
    statement = select(User).where(User.username == username).with_for_update()
    return session.exec(statement).first()
