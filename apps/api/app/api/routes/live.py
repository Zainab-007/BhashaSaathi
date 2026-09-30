from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from ...core.config import settings
from ...schemas import LiveWarmIn
from ...services.live_translation import LIVE_LANGUAGES, LiveTranslationSession, warm_live_models

router = APIRouter(prefix='/live', tags=['live-translation'])


@router.get('/languages')
def live_languages():
    return [{'code': code, **info} for code, info in LIVE_LANGUAGES.items()]


@router.post('/warm')
async def warm(x: LiveWarmIn):
    if not settings.stt_enabled or not settings.translation_enabled or not settings.tts_enabled:
        raise HTTPException(503, detail={'code': 'LIVE_AI_DISABLED', 'message': 'Live Translate requires STT, translation and TTS to be enabled.'})
    try:
        return await warm_live_models(x.source_language, x.target_language)
    except Exception as exc:
        raise HTTPException(503, detail={'code': 'LIVE_WARM_FAILED', 'message': 'Local live-translation models could not be prepared.', 'details': {'runtime_error': str(exc)[:600]}, 'retryable': True}) from exc


@router.websocket('/translate')
async def translate_socket(websocket: WebSocket):
    source = websocket.query_params.get('source', 'eng_Latn')
    target = websocket.query_params.get('target', 'hin_Deva')
    origin = websocket.headers.get('origin')
    allowed_origins = {item.strip() for item in settings.cors_origins.split(',') if item.strip()}
    if origin and origin not in allowed_origins:
        await websocket.close(code=1008, reason='Origin not allowed.')
        return
    if source not in LIVE_LANGUAGES or target not in LIVE_LANGUAGES or source == target:
        await websocket.accept()
        await websocket.send_text(json.dumps({'type': 'error', 'stage': 'config', 'message': 'Choose two different languages from English, Hindi, Marathi, or Santali.'}, ensure_ascii=False))
        await websocket.close(code=1008)
        return

    await websocket.accept()
    loop = asyncio.get_running_loop()
    session = LiveTranslationSession(source, target, loop)
    try:
        await session.initialize()
    except Exception as exc:
        await websocket.send_text(json.dumps({'type': 'error', 'stage': 'warmup', 'message': str(exc)[:600]}, ensure_ascii=False))
        await websocket.close(code=1011)
        return

    async def sender() -> None:
        while True:
            kind, payload = await session.outgoing.get()
            if websocket.client_state != WebSocketState.CONNECTED:
                return
            if kind == 'json':
                await websocket.send_text(json.dumps(payload, ensure_ascii=False))
            else:
                await websocket.send_bytes(payload)

    async def receiver() -> None:
        while True:
            message = await websocket.receive()
            if message['type'] == 'websocket.disconnect':
                raise WebSocketDisconnect(message.get('code', 1000))
            raw = message.get('bytes')
            if raw:
                sample_rate = int(websocket.query_params.get('sample_rate', '48000'))
                session.push_audio(raw, sample_rate)
                continue
            text = message.get('text') or ''
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            action = payload.get('action')
            if action == 'set_sample_rate':
                # The browser also puts the real rate in the URL at connect time;
                # this field is accepted for diagnostic visibility.
                continue
            if action == 'stop':
                session.flush()
                return
            if action == 'ping':
                session.emit_json({'type': 'pong'})

    send_task = asyncio.create_task(sender())
    receive_task = asyncio.create_task(receiver())
    try:
        done, pending = await asyncio.wait(
            {send_task, receive_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                raise exc
    except WebSocketDisconnect:
        pass
    finally:
        session.close()
        for task in (send_task, receive_task):
            if not task.done():
                task.cancel()
