import json,hashlib,shutil
from pathlib import Path
from fastapi import APIRouter,Depends,HTTPException,UploadFile,File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import *
from ..schemas import *
from ..security import current_user
from ..config import STORAGE_ROOT,settings
from ..services.analysis import analyze
from ..services.translation import translate,guard
from ..services.stt import transcribe
from ..services.tts import synthesize
from ..services.worksheet import make_practice,make_worksheet,make_flashcards
router=APIRouter(tags=["lessons"])

def get_lesson(db,id):
    x=db.get(Lesson,id)
    if not x: raise HTTPException(404,"Lesson not found")
    return x

def current_version(db,l):
    return db.get(LessonVersion,l.current_version_id) if l.current_version_id else None

def pack(db,l):
    v=current_version(db,l)
    if not v: return {"lesson":l.__dict__}
    trs=db.query(Translation).filter_by(lesson_version_id=v.id).all()
    arts=db.query(Artifact).filter_by(lesson_version_id=v.id).all()
    return {"lesson":{"id":l.id,"title":l.title,"status":l.status,"group_id":l.group_id},"version":{"id":v.id,"source_text":v.source_text,"clean_transcript":v.clean_transcript,"concepts":json.loads(v.concepts_json),"learning_objectives":json.loads(v.learning_objectives_json),"analysis":json.loads(v.ai_explanation_json)},"translations":[{"id":t.id,"language":t.target_language,"text":t.translated_text,"confidence":t.confidence,"flags":json.loads(t.flags_json),"verification_status":t.verification_status} for t in trs],"artifacts":[{"id":a.id,"type":a.type,"language":a.language,"url":f"/api/v1/artifacts/{a.id}"} for a in arts],"practice":make_practice(json.loads(v.concepts_json))}

@router.post("/groups/{group_id}/lessons")
def create(group_id:int,x:LessonIn,db:Session=Depends(get_db),u=Depends(current_user)):
    l=Lesson(group_id=group_id,title=x.title,source_language=x.source_language,grade=x.grade,subject=x.subject,topic=x.topic,created_by=u.id); db.add(l); db.flush(); v=LessonVersion(lesson_id=l.id,version_number=1,source_text=x.source_text,clean_transcript=x.source_text,created_by=u.id); db.add(v); db.flush(); l.current_version_id=v.id; db.commit(); return pack(db,l)
@router.get("/groups/{group_id}/lessons")
def list_lessons(group_id:int,db:Session=Depends(get_db),u=Depends(current_user)):
    return [{"id":l.id,"title":l.title,"status":l.status,"topic":l.topic,"group_id":l.group_id} for l in db.query(Lesson).filter_by(group_id=group_id).all()]
@router.get("/lessons/{lesson_id}")
def get(lesson_id:int,db:Session=Depends(get_db),u=Depends(current_user)): return pack(db,get_lesson(db,lesson_id))
@router.patch("/lessons/{lesson_id}")
def patch(lesson_id:int,x:LessonPatch,db:Session=Depends(get_db),u=Depends(current_user)):
    l=get_lesson(db,lesson_id); v=current_version(db,l)
    if x.title is not None:l.title=x.title
    if x.topic is not None:l.topic=x.topic
    if v:
        if x.source_text is not None:v.source_text=x.source_text
        if x.clean_transcript is not None:v.clean_transcript=x.clean_transcript
    db.commit(); return pack(db,l)
@router.post("/lessons/{lesson_id}/analyze")
def do_analyze(lesson_id:int,db:Session=Depends(get_db),u=Depends(current_user)):
    l=get_lesson(db,lesson_id);v=current_version(db,l); text=v.clean_transcript or v.source_text; a=analyze(text,l.grade,l.subject);v.concepts_json=json.dumps(a["concepts"],ensure_ascii=False);v.learning_objectives_json=json.dumps([a["learning_objective"]],ensure_ascii=False);v.ai_explanation_json=json.dumps(a,ensure_ascii=False);l.status="AI_SUGGESTED";db.commit();return a
@router.post("/lessons/{lesson_id}/translate")
def do_translate(lesson_id:int,x:TranslateIn,db:Session=Depends(get_db),u=Depends(current_user)):
    l=get_lesson(db,lesson_id);v=current_version(db,l); text=v.clean_transcript or v.source_text
    if settings.demo_mode:
        out={"sat_Olck":"ᱱᱟᱜ ᱦᱚᱲ ᱫᱚ ᱯᱟᱹᱱᱤ ᱞᱟᱹᱜᱤᱫ ᱡᱤᱣᱤ ᱠᱟᱛᱮ ᱜᱟᱹᱲᱤ ᱟᱹᱣᱟᱹᱜ ᱾","eng_Latn":"Plants need water to grow."}.get(x.target_language,text)
    else: out=translate(text,l.source_language,x.target_language)
    g=guard(text,out,l.source_language,x.target_language); t=Translation(lesson_version_id=v.id,source_language=l.source_language,target_language=x.target_language,translated_text=out,confidence=g["confidence"],flags_json=json.dumps(g["flags"]),verification_status=g["verification_status"]);db.add(t);l.status="FLAGGED" if g["flags"] else "TRANSLATED";db.commit();return {"translation_id":t.id,"text":out,**g}
@router.post("/lessons/{lesson_id}/verify-translation")
def verify(lesson_id:int,x:VerifyIn,db:Session=Depends(get_db),u=Depends(current_user)):
    t=db.get(Translation,x.translation_id);
    if not t: raise HTTPException(404,"Translation not found")
    if x.edited_text is not None:t.translated_text=x.edited_text;t.teacher_edited=True
    t.verification_status="TEACHER_APPROVED" if x.approved else "REJECTED";db.commit();return {"status":t.verification_status,"text":t.translated_text}
@router.post("/lessons/{lesson_id}/publish")
def publish(lesson_id:int,db:Session=Depends(get_db),u=Depends(current_user)):
    l=get_lesson(db,lesson_id);v=current_version(db,l); trs=db.query(Translation).filter_by(lesson_version_id=v.id).all()
    if not trs: raise HTTPException(400,"Translate at least one language before publishing")
    if any(t.verification_status!="TEACHER_APPROVED" for t in trs): raise HTTPException(400,"Every generated translation must be teacher-approved before publishing")
    l.status="PUBLISHED";db.commit();return pack(db,l)
@router.post("/lessons/{lesson_id}/transcribe")
async def do_transcribe(lesson_id:int,file:UploadFile=File(...),db:Session=Depends(get_db),u=Depends(current_user)):
    l=get_lesson(db,lesson_id);v=current_version(db,l); temp=STORAGE_ROOT/f"upload_{l.id}_{u.id}_{file.filename}";temp.write_bytes(await file.read())
    if settings.demo_mode: result={"text":"Good morning children. Today we are going to learn why plants need water.","language":"hi","probability":0.99}
    else: result=transcribe(temp,"hi")
    v.clean_transcript=result["text"];db.commit();return result
@router.post("/lessons/{lesson_id}/tts")
def do_tts(lesson_id:int,target_language:str="sat_Olck",db:Session=Depends(get_db),u=Depends(current_user)):
    l=get_lesson(db,lesson_id);v=current_version(db,l);t=db.query(Translation).filter_by(lesson_version_id=v.id,target_language=target_language).order_by(Translation.id.desc()).first()
    if not t: raise HTTPException(400,"Translate this language first")
    out=STORAGE_ROOT/f"lesson_{lesson_id}_v{v.version_number}_{target_language}.wav"
    if not out.exists():
        if settings.demo_mode: raise HTTPException(400,"Disable DEMO_MODE for real TTS")
        synthesize(t.translated_text,out,target_language)
    h=hashlib.sha256(out.read_bytes()).hexdigest();a=Artifact(lesson_version_id=v.id,type="audio",language=target_language,path=str(out),mime_type="audio/wav",sha256=h,size_bytes=out.stat().st_size);db.add(a);db.commit();return {"artifact_id":a.id,"url":f"/api/v1/artifacts/{a.id}"}
@router.get("/artifacts/{artifact_id}")
def artifact(artifact_id:int,db:Session=Depends(get_db),u=Depends(current_user)):
    a=db.get(Artifact,artifact_id)
    if not a: raise HTTPException(404,"Artifact not found")
    return FileResponse(a.path,media_type=a.mime_type)
@router.get("/lessons/{lesson_id}/worksheet")
def worksheet(lesson_id:int,db:Session=Depends(get_db),u=Depends(current_user)):
    l=get_lesson(db,lesson_id);v=current_version(db,l);return make_worksheet(l.title,json.loads(v.concepts_json))
@router.get("/lessons/{lesson_id}/flashcards")
def flashcards(lesson_id:int,db:Session=Depends(get_db),u=Depends(current_user)):
    l=get_lesson(db,lesson_id);v=current_version(db,l);return make_flashcards(json.loads(v.concepts_json))
