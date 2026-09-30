import sys, json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from app.db import SessionLocal, init_db
from app.models import User, Group, Membership, Lesson, LessonVersion
from app.core.security import hash_password

init_db(); db=SessionLocal()
try:
    teacher=db.query(User).filter_by(email='teacher@bhashasaathi.local').first()
    if not teacher:
        teacher=User(name='Demo Teacher',email='teacher@bhashasaathi.local',password_hash=hash_password('Demo@1234'),preferred_language='hin_Deva',account_role='admin'); db.add(teacher); db.flush()
    student=db.query(User).filter_by(email='student@bhashasaathi.local').first()
    if not student:
        student=User(name='Demo Student',email='student@bhashasaathi.local',password_hash=hash_password('Demo@1234'),preferred_language='mar_Deva',account_role='student'); db.add(student); db.flush()
    group=db.query(Group).filter_by(name='Demo Primary Classroom',owner_id=teacher.id).first()
    if not group:
        import secrets,string
        code=''.join(secrets.choice(string.ascii_uppercase+string.digits) for _ in range(7))
        group=Group(name='Demo Primary Classroom',join_code=code,grade='Grade 2',subject='Environmental Studies',owner_id=teacher.id); db.add(group); db.flush()
        db.add(Membership(group_id=group.id,user_id=teacher.id,role='admin'))
    if not db.query(Membership).filter_by(group_id=group.id,user_id=student.id).first(): db.add(Membership(group_id=group.id,user_id=student.id,role='student'))
    lesson=db.query(Lesson).filter_by(group_id=group.id,title='Why Plants Need Water').first()
    if not lesson:
        text='आज हम सीखेंगे कि पौधों को पानी की आवश्यकता क्यों होती है। पौधों को जीवित रहने और बढ़ने के लिए पानी चाहिए। पानी पौधों को स्वस्थ रहने में मदद करता है।'
        lesson=Lesson(group_id=group.id,title='Why Plants Need Water',grade='Grade 2',subject='Environmental Studies',topic='Plants and Water',source_language='hin_Deva',status='DRAFT',created_by=teacher.id); db.add(lesson); db.flush()
        v=LessonVersion(lesson_id=lesson.id,version_number=1,source_text=text,clean_transcript=text,learning_objectives_json=json.dumps(['Learner can explain that plants need water to live and grow.'],ensure_ascii=False),concepts_json=json.dumps([{'concept_id':'plants','label':'Plants','source_span':'पौधों','keywords':['plant','पौधे']},{'concept_id':'water','label':'Water','source_span':'पानी','keywords':['water','पानी']},{'concept_id':'growth','label':'Growth','source_span':'बढ़ने','keywords':['grow','बढ़ने']},{'concept_id':'need','label':'Need','source_span':'आवश्यकता','keywords':['need','आवश्यकता']}],ensure_ascii=False),created_by=teacher.id,ai_explanation_json=json.dumps({},ensure_ascii=False),activities_json='[]'); db.add(v); db.flush(); lesson.current_version_id=v.id
    db.commit()
    print('Teacher: teacher@bhashasaathi.local / Demo@1234')
    print('Student: student@bhashasaathi.local / Demo@1234')
    print('Group:',group.name)
    print('Join code:',group.join_code)
finally: db.close()
