import secrets,string
from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import Group,Membership,Lesson
from ..schemas import GroupIn,JoinIn
from ..security import current_user
router=APIRouter(tags=["groups"])
def code(): return "BS-"+"".join(secrets.choice(string.ascii_uppercase+string.digits) for _ in range(6))
@router.post("/groups")
def create(x:GroupIn,db:Session=Depends(get_db),u=Depends(current_user)):
    g=Group(name=x.name,grade=x.grade,subject=x.subject,join_code=code(),owner_id=u.id); db.add(g); db.flush(); db.add(Membership(group_id=g.id,user_id=u.id,role="admin")); db.commit(); return {"id":g.id,"name":g.name,"join_code":g.join_code,"grade":g.grade,"subject":g.subject}
@router.get("/groups")
def groups(db:Session=Depends(get_db),u=Depends(current_user)):
    ms=db.query(Membership).filter_by(user_id=u.id).all(); out=[]
    for m in ms:
        g=db.get(Group,m.group_id); out.append({"id":g.id,"name":g.name,"join_code":g.join_code,"grade":g.grade,"subject":g.subject,"role":m.role})
    return out
@router.post("/groups/join")
def join(x:JoinIn,db:Session=Depends(get_db),u=Depends(current_user)):
    g=db.query(Group).filter_by(join_code=x.join_code.strip().upper()).first()
    if not g: raise HTTPException(404,"Join code not found")
    if not db.query(Membership).filter_by(group_id=g.id,user_id=u.id).first(): db.add(Membership(group_id=g.id,user_id=u.id,role="student")); db.commit()
    return {"id":g.id,"name":g.name,"role":"student"}
