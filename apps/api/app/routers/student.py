import json
from fastapi import APIRouter,Depends
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import Attempt,Lesson,LessonVersion
from ..schemas import PracticeIn
from ..security import current_user
from .lessons import pack
router=APIRouter(prefix="/student",tags=["student"])
@router.get("/lessons/{lesson_id}/package")
def package(lesson_id:int,db:Session=Depends(get_db),u=Depends(current_user)):
    return pack(db,db.get(Lesson,lesson_id))
@router.post("/attempts")
def attempt(lesson_id:int,x:PracticeIn,db:Session=Depends(get_db),u=Depends(current_user)):
    l=db.get(Lesson,lesson_id);v=db.get(LessonVersion,l.current_version_id); concepts=json.loads(v.concepts_json); answers=x.answers; hits=[c for c in concepts if str(answers.get(c,"" )).lower()==str(c).lower()];score=(len(hits)/len(concepts)*100) if concepts else 0
    a=Attempt(lesson_id=l.id,lesson_version_id=v.id,student_id=u.id,answers_json=json.dumps(answers,ensure_ascii=False),score=score,concept_results_json=json.dumps({c:c in hits for c in concepts},ensure_ascii=False));db.add(a);db.commit();return {"score":score,"concept_results":json.loads(a.concept_results_json)}
