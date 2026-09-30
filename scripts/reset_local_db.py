from pathlib import Path
import sqlite3

root=Path(__file__).resolve().parents[1]/'apps/api'
db=root/'data'/'bhashasaathi.db'
if db.exists():
    db.unlink()
print(f'Removed {db} (if it existed). Now run:')
print('  cd apps\api')
print('  alembic upgrade head')
print(r'  python ..\..\scripts\seed_demo.py')
