from datetime import datetime, timedelta, timezone
import hashlib, secrets
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from .config import settings
from .database import get_db
from .models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def create_token(user: User) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode({"sub": str(user.id), "exp": expires}, settings.secret_key, algorithm="HS256")


def create_refresh_token() -> tuple[str,str]:
    raw=secrets.token_urlsafe(48)
    return raw,hashlib.sha256(raw.encode()).hexdigest()


def current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    error = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessão inválida ou expirada")
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise error
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise error
    return user


def admin_only(user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
    from .access import authorization
    context = authorization(user, db)
    if not context["is_admin"] or "system.manage" not in context["permissions"]:
        raise HTTPException(status_code=403, detail="Acesso exclusivo de administrador")
    return user


from .access import require_permission
manage_records=require_permission("customers.update")
operate_orders=require_permission("orders.change_status")
manage_finance=require_permission("financial.update")
