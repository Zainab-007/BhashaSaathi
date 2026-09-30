from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException

from ...core.security import current_user
from ...schemas import WarmTranslationIn
from ...services.model_manager import manager

router = APIRouter(prefix='/runtime', tags=['runtime'])


@router.post('/warm-translation')
def warm_translation(x: WarmTranslationIn, _user=Depends(current_user)):
    if x.source_language == x.target_language:
        return {'source': x.source_language, 'target': x.target_language, 'route': 'bypass', 'warm_ms': 0}
    started = time.perf_counter()
    try:
        result = manager.warm_translation(x.source_language, x.target_language)
    except Exception as exc:
        raise HTTPException(
            503,
            detail={
                'code': 'TRANSLATION_WARM_FAILED',
                'message': 'The local translation model could not be prepared.',
                'details': {'runtime_error': str(exc)[:600]},
                'retryable': True,
            },
        ) from exc
    result['source'] = x.source_language
    result['target'] = x.target_language
    result['warm_ms'] = round((time.perf_counter() - started) * 1000.0)
    return result


@router.post('/warm-tts')
def warm_tts(_user=Depends(current_user)):
    started = time.perf_counter()
    try:
        manager.tts.warm()
    except Exception as exc:
        raise HTTPException(
            503,
            detail={
                'code': 'TTS_WARM_FAILED',
                'message': 'The local voice model could not be prepared.',
                'details': {'runtime_error': str(exc)[:600]},
                'retryable': True,
            },
        ) from exc
    return {'ready': True, 'warm_ms': round((time.perf_counter() - started) * 1000.0)}
