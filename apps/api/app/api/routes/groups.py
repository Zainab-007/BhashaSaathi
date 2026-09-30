import secrets
import string
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ...db import get_db
from ...models import Group, Membership, User, Lesson
from ...schemas import GroupIn, JoinIn
from ...core.security import current_user

router = APIRouter(prefix='/groups', tags=['groups'])


def make_code() -> str:
    return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(7))


def group_view(db: Session, group: Group) -> dict:
    return {
        'id': group.id,
        'name': group.name,
        'join_code': group.join_code,
        'grade': group.grade,
        'subject': group.subject,
        'owner_id': group.owner_id,
        'members': db.query(Membership).filter_by(group_id=group.id).count(),
        'lessons': db.query(Lesson).filter_by(group_id=group.id).count(),
        'published_lessons': db.query(Lesson).filter_by(group_id=group.id, status='PUBLISHED').count(),
        'created_at': group.created_at.isoformat(),
    }


@router.post('')
def create_group(x: GroupIn, db: Session = Depends(get_db), user=Depends(current_user)):
    join = make_code()
    while db.query(Group).filter_by(join_code=join).first():
        join = make_code()
    group = Group(name=x.name.strip(), grade=x.grade.strip(), subject=x.subject.strip(), join_code=join, owner_id=user.id)
    db.add(group)
    db.flush()
    db.add(Membership(group_id=group.id, user_id=user.id, role='admin'))
    db.commit()
    db.refresh(group)
    return group_view(db, group)


@router.get('')
def list_groups(db: Session = Depends(get_db), user=Depends(current_user)):
    owned = db.query(Group).filter_by(owner_id=user.id).all()
    ids = [item.group_id for item in db.query(Membership).filter_by(user_id=user.id).all()]
    member_rows = db.query(Group).filter(Group.id.in_(ids or [-1])).all()
    by_id = {group.id: group for group in owned + member_rows}
    rows = sorted(by_id.values(), key=lambda item: item.id, reverse=True)
    return [group_view(db, group) for group in rows]


@router.get('/{group_id}')
def get_group(group_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    group = db.get(Group, group_id)
    if not group:
        raise HTTPException(404, detail={'code': 'GROUP_NOT_FOUND', 'message': 'Classroom not found.'})
    allowed = group.owner_id == user.id or bool(db.query(Membership).filter_by(group_id=group.id, user_id=user.id).first())
    if not allowed:
        raise HTTPException(403, detail={'code': 'GROUP_ACCESS_DENIED', 'message': 'You are not a member of this classroom.'})
    return group_view(db, group)


@router.post('/join')
def join_group(x: JoinIn, db: Session = Depends(get_db), user=Depends(current_user)):
    code = x.join_code.strip().upper()
    group = db.query(Group).filter_by(join_code=code).first()
    if not group:
        raise HTTPException(404, detail={'code': 'JOIN_CODE_INVALID', 'message': 'That join code is not valid.'})
    existing = db.query(Membership).filter_by(group_id=group.id, user_id=user.id).first()
    if existing:
        return {'already_member': True, 'group': group_view(db, group)}
    db.add(Membership(group_id=group.id, user_id=user.id, role='student'))
    db.commit()
    return {'already_member': False, 'group': group_view(db, group)}


@router.get('/{group_id}/members')
def members(group_id: int, db: Session = Depends(get_db), user=Depends(current_user)):
    group = db.get(Group, group_id)
    if not group:
        raise HTTPException(404, detail={'code': 'GROUP_NOT_FOUND', 'message': 'Classroom not found.'})
    if group.owner_id != user.id:
        raise HTTPException(403, detail={'code': 'ADMIN_REQUIRED', 'message': 'Only the classroom owner can manage members.'})
    rows = db.query(Membership, User).join(User, User.id == Membership.user_id).filter(Membership.group_id == group_id).all()
    return [
        {'id': membership.id, 'user_id': member.id, 'name': member.name, 'email': member.email, 'role': membership.role, 'joined_at': membership.joined_at.isoformat()}
        for membership, member in rows
    ]
