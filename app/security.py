from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    sub = request.session.get("user_sub")
    if not sub:
        return None
    return db.get(User, sub)
