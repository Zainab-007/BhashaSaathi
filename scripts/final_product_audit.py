from __future__ import annotations

from pathlib import Path
import ast
import sys

ROOT = Path(__file__).resolve().parents[1]

PY_FILES = [ROOT / 'apps' / 'api', ROOT / 'scripts', ROOT / 'core']
WEB = ROOT / 'apps' / 'web' / 'src'

checks: list[tuple[str, bool, str]] = []

def add(name: str, ok: bool, detail: str = '') -> None:
    checks.append((name, ok, detail))

# Python syntax
for base in PY_FILES:
    for path in base.rglob('*.py'):
        try:
            ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        except Exception as exc:
            add(f'Python syntax: {path.relative_to(ROOT)}', False, str(exc))
            break
    else:
        continue
    break
else:
    add('Python syntax tree parse', True)

# Product invariants
schemas = (ROOT / 'apps/api/app/schemas.py').read_text(encoding='utf-8')
entities = (ROOT / 'apps/api/app/models/entities.py').read_text(encoding='utf-8')
routes = (ROOT / 'apps/api/app/api/routes/lessons.py').read_text(encoding='utf-8')
web_builder = (ROOT / 'apps/web/src/pages/LessonBuilderPage.tsx').read_text(encoding='utf-8')
web_student = (ROOT / 'apps/web/src/pages/StudentLessonPage.tsx').read_text(encoding='utf-8')
live = (ROOT / 'apps/api/app/services/live_translation.py').read_text(encoding='utf-8')

add('Santali schema code', 'sat_Olck' in schemas)
add('Persisted source language', 'source_language' in entities)
add('Santali temporary status', "'TEMPORARY'" in routes)
add('Santali uses canonical STT route', 'manager.transcribe(path, lesson.source_language)' in routes)
add('TTS queue language bug fixed', "x.target_language == 'sat_Olck'" not in routes.split("/preparations/tts/{language}", 1)[-1].split("/preparations/{job_id}/retry", 1)[0])
add('Optional targets default to none', "setSelectedTargets([])" in web_builder)
add('No silent English target injection', "return ['eng_Latn'" not in web_builder)
add('Publish text-first', 'Optional translations, temporary Santali, practice, and audio are separate assets.' in web_builder)
add('Student exposes temporary Santali', "item.language === 'sat_Olck' && item.verification_status === 'TEMPORARY'" in web_student)
add('Live Santali route', "'sat_Olck'" in live and 'indic-conformer' in live)

failed = [x for x in checks if not x[1]]
for name, ok, detail in checks:
    print(('PASS' if ok else 'FAIL'), name, detail)

if failed:
    print(f'FAILED_CHECKS={len(failed)}')
    raise SystemExit(1)
print(f'FINAL_AUDIT=PASS checks={len(checks)}')
