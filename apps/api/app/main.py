import logging
import uuid
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from .core.config import settings, origins
from .db import init_db
from .api.routes import auth, groups, lessons, live, runtime, student, sync
from .services.model_manager import manager
from .services.background_prep import resume_pending_preparations

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
init_db()
resume_pending_preparations()

app = FastAPI(
    title='BhashaSaathi Local API',
    version=settings.app_version,
    description='Teacher-controlled multilingual classroom learning for SIH26042.',
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins(),
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.middleware('http')
async def request_context(request: Request, call_next):
    request_id = str(uuid.uuid4())
    try:
        response = await call_next(request)
        response.headers['X-Request-ID'] = request_id
        return response
    except Exception:
        logging.exception('request_id=%s path=%s', request_id, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                'request_id': request_id,
                'code': 'INTERNAL_ERROR',
                'message': 'Something went wrong. Your saved lesson content was preserved.',
                'details': {},
                'retryable': True,
            },
        )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    request_id = request.headers.get('X-Request-ID') or str(uuid.uuid4())
    fields = []
    for error in exc.errors():
        location = '.'.join(str(part) for part in error.get('loc', []) if part != 'body')
        fields.append({'field': location, 'message': error.get('msg', 'Invalid value.')})
    return JSONResponse(
        status_code=422,
        headers={'X-Request-ID': request_id},
        content={
            'request_id': request_id,
            'code': 'VALIDATION_ERROR',
            'message': 'Please check the highlighted fields and try again.',
            'details': {'fields': fields},
            'retryable': False,
        },
    )


app.include_router(auth.router, prefix='/api/v1')
app.include_router(groups.router, prefix='/api/v1')
app.include_router(lessons.router, prefix='/api/v1')
app.include_router(student.router, prefix='/api/v1')
app.include_router(sync.router, prefix='/api/v1')
app.include_router(live.router, prefix='/api/v1')
app.include_router(runtime.router, prefix='/api/v1')


@app.get('/api/v1/health')
def health():
    return {'status': 'ok', 'product': 'BhashaSaathi', 'version': settings.app_version, 'mode': 'local'}


@app.get('/api/v1/health/models')
def model_health():
    return manager.status()
