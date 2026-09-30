from datetime import datetime, timedelta, timezone
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from .config import settings
from .db import get_db
from .models import User

ph=PasswordHasher()
bearer=HTTPBearer(auto_error=False)

def hash_password(p): return ph.hash(p)
def verify_password(h,p):
    try: return ph.verify(h,p)
    except VerifyMismatchError: return False

def create_token(user):
    exp=datetime.now(timezone.utc)+timedelta(minutes=settings.jwt_expire_minutes)
    return jwt.encode({"sub":str(user.id),"email":user.email,"exp":exp},settings.jwt_secret,algorithm="HS256")

def current_user(creds:HTTPAuthorizationCredentials=Depends(bearer), db:Session=Depends(get_db)):
    if not creds: raise HTTPException(status_code=401, detail="Authentication required")
    try: payload=jwt.decode(creds.credentials,settings.jwt_secret,algorithms=["HS256"])
    except jwt.PyJWTError: raise HTTPException(status_code=401, detail="Invalid or expired token")
    user=db.get(User,int(payload["sub"]))
    if not user: raise HTTPException(status_code=401, detail="User not found")
    return user
