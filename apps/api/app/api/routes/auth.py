from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ...db import get_db
from ...models import User
from ...schemas import RegisterIn, LoginIn
from ...core.security import hash_password, verify_password, create_access_token, current_user

router=APIRouter(prefix='/auth',tags=['auth'])

def user_view(u): return {'id':u.id,'name':u.name,'email':u.email,'preferred_language':u.preferred_language,'account_role':u.account_role}

@router.post('/register')
def register(x:RegisterIn, db:Session=Depends(get_db)):
    if db.query(User).filter_by(email=x.email.lower()).first(): raise HTTPException(409, detail={'code':'EMAIL_EXISTS','message':'Email is already registered.'})
    u=User(name=x.name.strip(),email=x.email.lower(),password_hash=hash_password(x.password),preferred_language='eng_Latn',account_role='user')
    db.add(u); db.commit(); db.refresh(u)
    return {'access_token':create_access_token(u),'user':user_view(u)}

@router.post('/login')
def login(x:LoginIn, db:Session=Depends(get_db)):
    u=db.query(User).filter_by(email=x.email.lower()).first()
    if not u or not verify_password(u.password_hash,x.password): raise HTTPException(401, detail={'code':'INVALID_CREDENTIALS','message':'Invalid email or password.'})
    return {'access_token':create_access_token(u),'user':user_view(u)}

@router.post('/logout')
def logout(): return {'ok':True}

@router.get('/me')
def me(u=Depends(current_user)): return user_view(u)
