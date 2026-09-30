from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import User
from ..schemas import RegisterIn,LoginIn
from ..security import hash_password,verify_password,create_token,current_user
router=APIRouter(prefix="/auth",tags=["auth"])
@router.post("/register")
def register(x:RegisterIn,db:Session=Depends(get_db)):
    if db.query(User).filter_by(email=x.email).first(): raise HTTPException(409,"Email already registered")
    u=User(name=x.name,email=x.email,password_hash=hash_password(x.password),preferred_language=x.preferred_language); db.add(u); db.commit(); db.refresh(u)
    return {"access_token":create_token(u),"user":{"id":u.id,"name":u.name,"email":u.email,"preferred_language":u.preferred_language}}
@router.post("/login")
def login(x:LoginIn,db:Session=Depends(get_db)):
    u=db.query(User).filter_by(email=x.email).first()
    if not u or not verify_password(u.password_hash,x.password): raise HTTPException(401,"Invalid email or password")
    return {"access_token":create_token(u),"user":{"id":u.id,"name":u.name,"email":u.email,"preferred_language":u.preferred_language}}
@router.get("/me")
def me(u=Depends(current_user)): return {"id":u.id,"name":u.name,"email":u.email,"preferred_language":u.preferred_language}
