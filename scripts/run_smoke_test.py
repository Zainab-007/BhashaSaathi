import sys, urllib.request, json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/api'))
from app.db import init_db, SessionLocal
from app.models import User, Group, Lesson
init_db(); db=SessionLocal()
try:
    assert db.query(User).filter_by(email='teacher@bhashasaathi.local').first(), 'Seed teacher missing'
    assert db.query(User).filter_by(email='student@bhashasaathi.local').first(), 'Seed student missing'
    assert db.query(Group).filter_by(name='Demo Primary Classroom').first(), 'Demo group missing'
    assert db.query(Lesson).filter_by(title='Why Plants Need Water').first(), 'Demo lesson missing'
finally: db.close()
print('DB smoke checks: PASS')
print('Start the server and use GET http://127.0.0.1:8000/api/v1/health to verify HTTP health.')
