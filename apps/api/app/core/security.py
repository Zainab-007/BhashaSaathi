from datetime import datetime,timedelta,timezone
from typing import Any
import jwt
from argon2 import PasswordHasher
from fastapi import Depends,HTTPException,status
from fastapi.security import HTTPAuthorizationCredentials,HTTPBearer
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import User
from .config import settings

_passwords=PasswordHasher(); _bearer=HTTPBearer(auto_error=False)

def hash_password(password:str)->str: return _passwords.hash(password)
def verify_password(encoded:str,plain:str)->bool:
    try: return _passwords.verify(encoded,plain)
    except Exception: return False
def create_access_token(user:User)->str:
    now=datetime.now(timezone.utc); payload={'sub':str(user.id),'role':user.account_role,'exp':now+timedelta(hours=8),'iat':now}
    return jwt.encode(payload,settings.secret_key,algorithm='HS256')
def current_user(credentials:HTTPAuthorizationCredentials|None=Depends(_bearer),db:Session=Depends(get_db))->User:
    if not credentials: raise HTTPException(401,detail={'code':'AUTH_REQUIRED','message':'Please sign in.'})
    try: payload:dict[str,Any]=jwt.decode(credentials.credentials,settings.secret_key,algorithms=['HS256']); user_id=int(payload['sub'])
    except Exception: raise HTTPException(401,detail={'code':'AUTH_INVALID','message':'Your session is invalid or expired.'})
    user=db.get(User,user_id)
    if not user: raise HTTPException(401,detail={'code':'USER_NOT_FOUND','message':'Account no longer exists.'})
    return user
def require_admin(user:User=Depends(current_user))->User:
    if user.account_role!='admin': raise HTTPException(403,detail={'code':'ADMIN_REQUIRED','message':'Administrator access is required.'})
    return user
