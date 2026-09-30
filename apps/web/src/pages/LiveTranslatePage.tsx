import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import {
  AudioLines, CircleStop, Headphones, History, Languages, Mic, Play, RefreshCw,
  Save, ShieldCheck, Trash2, Wifi, WifiOff, X, ArrowDownUp, Volume2,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { Page, SectionTitle } from '../components/Shell';
import { Button, Card, Field, Status, useToast } from '../components/UI';
import { api, errorMessage } from '../lib/api';
import { deleteLiveSession, listLiveHistory, saveLiveSession, setLiveSaved, type LiveHistoryEntry, type LiveTurn } from '../lib/liveHistory';
import { LANGS, type Lang } from '../types';

const LIVE_LANGS: Lang[] = ['eng_Latn', 'hin_Deva', 'mar_Deva', 'sat_Olck'];
const label = (lang: Lang) => LANGS[lang].name;
const nativeLabel = (lang: Lang) => LANGS[lang].native;

interface DeviceChoice { deviceId: string; label: string; kind: MediaDeviceKind }

function downsampleFloat32(input: Float32Array, inputRate: number, outputRate = 16000) {
  if (inputRate === outputRate) return input;
  const ratio = inputRate / outputRate;
  const outputLength = Math.round(input.length / ratio);
  const result = new Float32Array(outputLength);
  let offset = 0;
  for (let index = 0; index < outputLength; index += 1) {
    const nextOffset = Math.min(input.length, Math.round((index + 1) * ratio));
    let sum = 0;
    let count = 0;
    for (let sourceIndex = offset; sourceIndex < nextOffset; sourceIndex += 1) {
      sum += input[sourceIndex];
      count += 1;
    }
    result[index] = count ? sum / count : 0;
    offset = nextOffset;
  }
  return result;
}

function floatToPcm16(input: Float32Array) {
  const buffer = new ArrayBuffer(input.length * 2);
  const view = new DataView(buffer);
  for (let index = 0; index < input.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, input[index]));
    view.setInt16(index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }
  return buffer;
}

function pcmFloatChunksToWav(chunks: Float32Array[], sampleRate: number) {
  const length = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const buffer = new ArrayBuffer(44 + length * 2);
  const view = new DataView(buffer);
  const writeString = (offset: number, value: string) => {
    for (let index = 0; index < value.length; index += 1) view.setUint8(offset + index, value.charCodeAt(index));
  };
  writeString(0, 'RIFF');
  view.setUint32(4, 36 + length * 2, true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, 'data');
  view.setUint32(40, length * 2, true);
  let offset = 44;
  for (const chunk of chunks) {
    for (let index = 0; index < chunk.length; index += 1) {
      const sample = Math.max(-1, Math.min(1, chunk[index]));
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
      offset += 2;
    }
  }
  return new Blob([buffer], { type: 'audio/wav' });
}

export default function LiveTranslatePage() {
  const { push } = useToast();
  const [source, setSource] = useState<Lang>(() => (localStorage.getItem('bhashasaathi:live-source') as Lang) || 'hin_Deva');
  const [target, setTarget] = useState<Lang>(() => (localStorage.getItem('bhashasaathi:live-target') as Lang) || 'sat_Olck');
  const [status, setStatus] = useState<'idle' | 'warming' | 'ready' | 'listening' | 'stopping' | 'error'>('idle');
  const [statusText, setStatusText] = useState('Ready when you are.');
  const [transcript, setTranscript] = useState('');
  const [translation, setTranslation] = useState('');
  const [latency, setLatency] = useState<{ stt?: number; translation?: number; tts?: number; total?: number }>({});
  const [warmMs, setWarmMs] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [micDevices, setMicDevices] = useState<DeviceChoice[]>([]);
  const [outputDevices, setOutputDevices] = useState<DeviceChoice[]>([]);
  const [micId, setMicId] = useState('');
  const [outputId, setOutputId] = useState('');
  const [history, setHistory] = useState<LiveHistoryEntry[]>([]);
  const [selectedHistory, setSelectedHistory] = useState<string | null>(null);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<LiveTurn[]>([]);
  const [playing, setPlaying] = useState(false);
  const [audioGenerating, setAudioGenerating] = useState(false);
  const [audioReady, setAudioReady] = useState(false);
  const currentTurnsRef = useRef<Map<number, LiveTurn>>(new Map());
  const pendingAudioRef = useRef<Map<number, Blob>>(new Map());
  const currentSessionRef = useRef<LiveHistoryEntry | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const zeroGainRef = useRef<GainNode | null>(null);
  const playbackAudioRef = useRef<HTMLAudioElement | null>(null);
  const playbackContextRef = useRef<AudioContext | null>(null);
  const playbackDestinationRef = useRef<MediaStreamAudioDestinationNode | null>(null);
  const playbackNextTimeRef = useRef(0);
  const activeAudioModeRef = useRef<'wav' | 'pcm'>('wav');
  const activeAudioRateRef = useRef(44100);
  const pendingPcmRef = useRef<Map<number, Float32Array[]>>(new Map());
  const playbackQueueRef = useRef<Array<{ sequence: number; blob: Blob }>>([]);
  const playingRef = useRef(false);
  const stoppingRef = useRef(false);
  const pendingSequenceRef = useRef<number | null>(null);

  const refreshHistory = useCallback(async () => {
    setHistory(await listLiveHistory());
  }, []);

  const refreshDevices = useCallback(async () => {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    const devices = await navigator.mediaDevices.enumerateDevices();
    const inputs = devices.filter((device) => device.kind === 'audioinput').map((device) => ({ deviceId: device.deviceId, label: device.label || 'Microphone', kind: device.kind }));
    const outputs = devices.filter((device) => device.kind === 'audiooutput').map((device) => ({ deviceId: device.deviceId, label: device.label || 'Speaker / Headphones', kind: device.kind }));
    setMicDevices(inputs);
    setOutputDevices(outputs);
    if (!micId && inputs.length) setMicId(inputs.find((device) => /microphone|mic|internal/i.test(device.label))?.deviceId || inputs[0].deviceId);
    if (!outputId && outputs.length) setOutputId(outputs.find((device) => /buds|headphone|headset|airpods|earphone/i.test(device.label))?.deviceId || outputs[0].deviceId);
  }, [micId, outputId]);

  useEffect(() => {
    void refreshHistory();
    void refreshDevices();
  }, [refreshHistory, refreshDevices]);

  const setOutputSink = useCallback(async () => {
    const audio = playbackAudioRef.current;
    if (!audio || !outputId) return;
    const sinkAudio = audio as HTMLAudioElement & { setSinkId?: (id: string) => Promise<void> };
    if (sinkAudio.setSinkId) {
      try { await sinkAudio.setSinkId(outputId); } catch { /* Browser does not support or device disappeared. */ }
    }
  }, [outputId]);

  useEffect(() => { void setOutputSink(); }, [setOutputSink]);

  const warm = useCallback(async (nextSource = source, nextTarget = target) => {
    if (nextSource === nextTarget) return;
    setStatus('warming');
    setStatusText('Preparing local speech, translation and voice models…');
    setError('');
    try {
      const result = await api.warmLive(nextSource, nextTarget);
      setWarmMs(result.warm_ms);
      setStatus('ready');
      setStatusText('Models are warm. Start speaking.');
    } catch (err) {
      setStatus('error');
      setError(errorMessage(err));
      setStatusText('Local model preparation failed.');
    }
  }, [source, target]);

  useEffect(() => {
    try {
      localStorage.setItem('bhashasaathi:live-source', source);
      localStorage.setItem('bhashasaathi:live-target', target);
    } catch { /* local preference only */ }
  }, [source, target]);

  useEffect(() => { void warm(source, target); }, [source, target, warm]);

  const swapLanguages = () => {
    setSource(target);
    setTarget(source);
    setTranscript('');
    setTranslation('');
    setLatency({});
  };

  const enqueuePlayback = useCallback(async (sequence: number, blob: Blob) => {
    playbackQueueRef.current.push({ sequence, blob });
    if (playingRef.current) return;
    playingRef.current = true;
    setPlaying(true);
    while (playbackQueueRef.current.length) {
      const current = playbackQueueRef.current.shift()!;
      const audio = playbackAudioRef.current;
      if (!audio) break;
      await setOutputSink();
      audio.srcObject = null;
      audio.src = URL.createObjectURL(current.blob);
      try {
        await audio.play();
        await new Promise<void>((resolve) => { audio.onended = () => resolve(); audio.onerror = () => resolve(); });
      } catch {
        // Autoplay/device policy can block playback; the user can still use the controls.
      } finally {
        URL.revokeObjectURL(audio.src);
      }
    }
    playingRef.current = false;
    setPlaying(false);
  }, [setOutputSink]);

  const ensurePcmPlayback = useCallback(async () => {
    const audio = playbackAudioRef.current;
    if (!audio) throw new Error('Playback element is unavailable.');
    let context = playbackContextRef.current;
    let destination = playbackDestinationRef.current;
    if (!context || context.state === 'closed' || !destination) {
      context = new AudioContext();
      destination = context.createMediaStreamDestination();
      audio.src = '';
      audio.srcObject = destination.stream;
      audio.autoplay = true;
      audio.muted = false;
      playbackContextRef.current = context;
      playbackDestinationRef.current = destination;
      playbackNextTimeRef.current = context.currentTime + 0.05;
    }
    await setOutputSink();
    await context.resume();
    try { await audio.play(); } catch { /* user gesture policy; context still remains ready */ }
    return { context, destination };
  }, [setOutputSink]);

  const schedulePcmChunk = useCallback(async (payload: ArrayBuffer, sampleRate: number) => {
    const { context, destination } = await ensurePcmPlayback();
    const samples = new Float32Array(payload);
    if (!samples.length) return;
    const audioBuffer = context.createBuffer(1, samples.length, sampleRate);
    audioBuffer.copyToChannel(samples, 0);
    const sourceNode = context.createBufferSource();
    sourceNode.buffer = audioBuffer;
    sourceNode.connect(destination);
    const startAt = Math.max(playbackNextTimeRef.current, context.currentTime + 0.03);
    sourceNode.start(startAt);
    playbackNextTimeRef.current = startAt + audioBuffer.duration;
    playingRef.current = true;
    setPlaying(true);
    sourceNode.onended = () => {
      if (context.currentTime + 0.03 >= playbackNextTimeRef.current) {
        playingRef.current = false;
        setPlaying(false);
      }
    };
  }, [ensurePcmPlayback]);

  const persistCurrentSession = useCallback(async (saved?: boolean) => {
    const current = currentSessionRef.current;
    if (!current) return;
    const updated: LiveHistoryEntry = {
      ...current,
      endedAt: Date.now(),
      turns: Array.from(currentTurnsRef.current.values()).sort((a, b) => a.sequence - b.sequence),
      saved: saved ?? current.saved,
      expiresAt: (saved ?? current.saved) ? Number.MAX_SAFE_INTEGER : Date.now() + 24 * 60 * 60 * 1000,
    };
    currentSessionRef.current = updated;
    await saveLiveSession(updated);
    await refreshHistory();
  }, [refreshHistory]);

  const stop = useCallback(async () => {
    stoppingRef.current = true;
    setStatus('stopping');
    const ws = wsRef.current;
    try { ws?.send(JSON.stringify({ action: 'stop' })); } catch { /* already closed */ }
    try { ws?.close(); } catch { /* noop */ }
    wsRef.current = null;
    playbackQueueRef.current = [];
    playingRef.current = false;
    setPlaying(false);
    setAudioGenerating(false);
    setAudioReady(false);
    const playbackAudio = playbackAudioRef.current;
    playbackAudio?.pause();
    if (playbackAudio) { playbackAudio.srcObject = null; playbackAudio.src = ''; }
    if (playbackContextRef.current) await playbackContextRef.current.close().catch(() => undefined);
    playbackContextRef.current = null;
    playbackDestinationRef.current = null;
    playbackNextTimeRef.current = 0;
    pendingPcmRef.current.clear();
    processorRef.current?.disconnect();
    zeroGainRef.current?.disconnect();
    processorRef.current = null;
    zeroGainRef.current = null;
    for (const track of mediaStreamRef.current?.getTracks() || []) track.stop();
    mediaStreamRef.current = null;
    if (audioContextRef.current) await audioContextRef.current.close().catch(() => undefined);
    audioContextRef.current = null;
    await persistCurrentSession();
    setStatus('ready');
    setStatusText('Session stopped. Unsaved history expires automatically after 24 hours.');
    stoppingRef.current = false;
  }, [persistCurrentSession, setOutputSink]);

  const start = async () => {
    if (status === 'listening' || status === 'warming') return;
    setError('');
    if (!navigator.mediaDevices?.getUserMedia) {
      setError('This browser does not provide microphone access. Use Chrome on localhost for the demo.');
      return;
    }
    if (source === target) {
      setError('Choose two different languages.');
      return;
    }
    try {
      await refreshDevices();
      setStatus('warming');
      setStatusText('Requesting microphone access…');
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          deviceId: micId ? { exact: micId } : undefined,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      });
      mediaStreamRef.current = stream;
      const context = new AudioContext();
      audioContextRef.current = context;
      await context.resume();
      const playbackContext = new AudioContext();
      const playbackDestination = playbackContext.createMediaStreamDestination();
      const playbackAudio = playbackAudioRef.current;
      if (playbackAudio) {
        playbackAudio.src = '';
        playbackAudio.srcObject = playbackDestination.stream;
        playbackAudio.autoplay = true;
      }
      playbackContextRef.current = playbackContext;
      playbackDestinationRef.current = playbackDestination;
      playbackNextTimeRef.current = playbackContext.currentTime + 0.05;
      await setOutputSink();
      await playbackContext.resume();
      const sampleRate = context.sampleRate;
      const queryBase = api.base.replace(/^http/, 'ws');
      const ws = new WebSocket(`${queryBase}/live/translate?source=${encodeURIComponent(source)}&target=${encodeURIComponent(target)}&sample_rate=16000`);
      ws.binaryType = 'arraybuffer';
      wsRef.current = ws;

      const session: LiveHistoryEntry = {
        id: `live-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
        source,
        target,
        startedAt: Date.now(),
        endedAt: Date.now(),
        saved: false,
        expiresAt: Date.now() + 24 * 60 * 60 * 1000,
        turns: [],
      };
      currentSessionRef.current = session;
      currentTurnsRef.current = new Map();
      pendingAudioRef.current = new Map();
      setCurrentSessionId(session.id);
      setTurns([]);
      setTranscript('');
      setTranslation('');
      setAudioGenerating(false);
      setAudioReady(false);

      ws.onopen = () => {
        setStatus('listening');
        setStatusText('Listening… speak naturally.');
        const sourceNode = context.createMediaStreamSource(stream);
        const processor = context.createScriptProcessor(2048, 1, 1);
        const silent = context.createGain();
        silent.gain.value = 0;
        processor.onaudioprocess = (event) => {
          if (ws.readyState !== WebSocket.OPEN) return;
          const downsampled = downsampleFloat32(event.inputBuffer.getChannelData(0), sampleRate, 16000);
          ws.send(floatToPcm16(downsampled));
        };
        sourceNode.connect(processor);
        processor.connect(silent);
        silent.connect(context.destination);
        processorRef.current = processor;
        zeroGainRef.current = silent;
      };

      ws.onmessage = async (event) => {
        if (typeof event.data === 'string') {
          const payload = JSON.parse(event.data);
          if (payload.type === 'status') {
            setStatus(payload.status === 'ready' || payload.status === 'listening' ? payload.status : 'warming');
            setStatusText(payload.message || 'Working…');
          } else if (payload.type === 'transcript') {
            setTranscript(payload.text || '');
            setLatency((old) => ({ ...old, stt: payload.stt_ms }));
            const existing = currentTurnsRef.current.get(payload.sequence) || { id: `${session.id}-${payload.sequence}`, sequence: payload.sequence, transcript: '', translation: '', createdAt: Date.now() };
            currentTurnsRef.current.set(payload.sequence, { ...existing, transcript: payload.text || '' });
          } else if (payload.type === 'translation') {
            setTranslation(payload.text || '');
            setLatency((old) => ({ ...old, translation: payload.translation_ms }));
            const existing = currentTurnsRef.current.get(payload.sequence) || { id: `${session.id}-${payload.sequence}`, sequence: payload.sequence, transcript: '', translation: '', createdAt: Date.now() };
            currentTurnsRef.current.set(payload.sequence, { ...existing, translation: payload.text || '' });
          } else if (payload.type === 'audio_start') {
            pendingSequenceRef.current = payload.sequence;
            setAudioGenerating(true);
            setAudioReady(false);
            activeAudioModeRef.current = payload.mode === 'pcm' ? 'pcm' : 'wav';
            activeAudioRateRef.current = Number(payload.sample_rate) || 44100;
            pendingPcmRef.current.set(payload.sequence, []);
            setLatency((old) => ({ ...old, tts: payload.tts_ms ?? payload.time_to_first_audio_ms, total: payload.total_ms }));
          } else if (payload.type === 'audio_ready') {
            setAudioGenerating(false);
            setAudioReady(Boolean(payload.has_audio));
          } else if (payload.type === 'audio_end') {
            const current = currentTurnsRef.current.get(payload.sequence);
            if (current) {
              let audio = pendingAudioRef.current.get(payload.sequence);
              if (activeAudioModeRef.current === 'pcm') {
                const chunks = pendingPcmRef.current.get(payload.sequence) || [];
                if (chunks.length) audio = pcmFloatChunksToWav(chunks, activeAudioRateRef.current);
                pendingPcmRef.current.delete(payload.sequence);
              }
              const completed: LiveTurn = {
                ...current,
                translation: payload.translation || current.translation,
                audio,
                totalMs: typeof payload.total_ms === 'number' ? payload.total_ms : undefined,
              };
              currentTurnsRef.current.set(payload.sequence, completed);
              setLatency((old) => ({ ...old, tts: payload.tts_ms ?? old.tts, total: payload.total_ms ?? old.total }));
              setAudioGenerating(false);
              setAudioReady(Boolean(audio));
              setTurns(Array.from(currentTurnsRef.current.values()).sort((a, b) => a.sequence - b.sequence));
              void persistCurrentSession();
            }
            pendingSequenceRef.current = null;
          } else if (payload.type === 'error') {
            pendingSequenceRef.current = null;
            pendingPcmRef.current.clear();
            playingRef.current = false;
            setPlaying(false);
            setError(`${payload.stage || 'pipeline'}: ${payload.message || 'Unknown error'}`);
            setStatus('error');
            setAudioGenerating(false);
          }
          return;
        }
        const sequence = pendingSequenceRef.current;
        if (typeof sequence === 'number') {
          if (activeAudioModeRef.current === 'pcm') {
            const chunk = new Float32Array(event.data);
            const chunks = pendingPcmRef.current.get(sequence) || [];
            chunks.push(chunk);
            pendingPcmRef.current.set(sequence, chunks);
            await schedulePcmChunk(event.data, activeAudioRateRef.current);
          } else {
            const blob = new Blob([event.data], { type: 'audio/wav' });
            pendingAudioRef.current.set(sequence, blob);
            const current = currentTurnsRef.current.get(sequence);
            if (current) {
              currentTurnsRef.current.set(sequence, { ...current, audio: blob });
              setTurns(Array.from(currentTurnsRef.current.values()).sort((a, b) => a.sequence - b.sequence));
            }
            await enqueuePlayback(sequence, blob);
          }
        }
      };
      ws.onerror = () => {
        setError('Live connection failed. Keep FastAPI running and try again.');
        setStatus('error');
      };
      ws.onclose = () => {
        if (wsRef.current !== ws) return;
        wsRef.current = null;
        processorRef.current?.disconnect();
        zeroGainRef.current?.disconnect();
        processorRef.current = null;
        zeroGainRef.current = null;
        for (const track of mediaStreamRef.current?.getTracks() || []) track.stop();
        mediaStreamRef.current = null;
        if (audioContextRef.current) void audioContextRef.current.close().catch(() => undefined);
        audioContextRef.current = null;
        if (!stoppingRef.current) {
          void persistCurrentSession();
          setStatus((currentStatus) => currentStatus === 'error' ? currentStatus : 'ready');
          setStatusText('Live session ended. You can start again.');
        }
      };
    } catch (err) {
      setError(errorMessage(err));
      setStatus('error');
      setStatusText('Live Translate could not start.');
      await stop();
    }
  };

  const saveCurrent = async () => {
    if (!currentSessionId) return;
    await setLiveSaved(currentSessionId, true);
    if (currentSessionRef.current) currentSessionRef.current.saved = true;
    await refreshHistory();
    push('This Live Translate session is saved on this device.');
  };

  const deleteHistory = async (id: string) => {
    await deleteLiveSession(id);
    if (currentSessionId === id) {
      currentSessionRef.current = null;
      setCurrentSessionId(null);
    }
    await refreshHistory();
  };

  const replay = async (turn: LiveTurn) => {
    if (!turn.audio) return;
    const blob = turn.audio;
    const audio = playbackAudioRef.current;
    if (!audio) return;
    await setOutputSink();
    audio.srcObject = null;
    const url = URL.createObjectURL(blob);
    audio.src = url;
    try { await audio.play(); } catch { /* browser playback policy */ }
    finally { URL.revokeObjectURL(url); }
  };

  const selectedEntry = history.find((entry) => entry.id === selectedHistory) || null;

  return <>
    <audio ref={playbackAudioRef} hidden />
    <Page eyebrow="LIVE TRANSLATE" title="Speak naturally. Hear the translation." subtitle="A browser-native local translator for English, Hindi, Marathi and Santali. No account required.">
      <div className="live-topline">
        <div className="live-topline-copy"><Status tone={status === 'error' ? 'danger' : status === 'listening' ? 'success' : 'info'}>{status === 'listening' ? 'LIVE' : status.toUpperCase()}</Status><span>{statusText}</span></div>
        <div className="head-action-row"><Link to="/"><Button variant="ghost">Back home</Button></Link><Button variant="secondary" onClick={() => void refreshDevices()}><RefreshCw size={15} /> Refresh devices</Button></div>
      </div>
      {error && <div className="error-banner"><X size={18} /><div><strong>Live Translate needs attention</strong><span>{error}</span></div><button onClick={() => setError('')} aria-label="Dismiss"><X size={15} /></button></div>}

      <div className="live-hero-grid">
        <Card className="live-control-card">
          <SectionTitle title="Language pair" subtitle="Only the validated demo languages are exposed here." />
          <div className="live-language-pair">
            <Field label="You speak" hint="Saved with this live session so it can be replayed in the same direction."><select value={source} onChange={(event) => setSource(event.target.value as Lang)} disabled={status === 'listening'}>{LIVE_LANGS.map((lang) => <option key={lang} value={lang}>{nativeLabel(lang)} · {label(lang)}</option>)}</select></Field>
            <button className="swap-circle" onClick={swapLanguages} disabled={status === 'listening'} aria-label="Swap languages"><ArrowDownUp size={18} /></button>
            <Field label="Listener hears" hint="Swap to reverse the direction."><select value={target} onChange={(event) => setTarget(event.target.value as Lang)} disabled={status === 'listening'}>{LIVE_LANGS.map((lang) => <option key={lang} value={lang}>{nativeLabel(lang)} · {label(lang)}</option>)}</select></Field>
          </div>

          <div className="device-grid">
            <Field label="Microphone" hint="Use the laptop's built-in mic for the demo."><select value={micId} onChange={(event) => setMicId(event.target.value)} disabled={status === 'listening'}>{micDevices.length ? micDevices.map((device) => <option key={device.deviceId || 'default'} value={device.deviceId}>{device.label}</option>) : <option value="">Browser default microphone</option>}</select></Field>
            <Field label="Output" hint="Use your Buds / headphones to avoid acoustic feedback."><select value={outputId} onChange={(event) => setOutputId(event.target.value)}>{outputDevices.length ? outputDevices.map((device) => <option key={device.deviceId || 'default'} value={device.deviceId}>{device.label}</option>) : <option value="">System default output</option>}</select></Field>
          </div>

          <div className="live-actions"><Button size="lg" onClick={() => void start()} disabled={status === 'listening' || status === 'warming' || source === target}><Mic size={18} /> Start Live Translation</Button><Button size="lg" variant="danger" onClick={() => void stop()} disabled={status !== 'listening'}><CircleStop size={18} /> Stop</Button></div>
          <div className="live-trust-row"><span><ShieldCheck size={15} /> Local pipeline</span><span><WifiOff size={15} /> No account required</span><span><Headphones size={15} /> Buds output recommended</span></div>
          {warmMs !== null && <div className="performance-strip"><span>Warm-up {warmMs.toLocaleString()} ms</span><span>STT {latency.stt ?? '—'} ms</span><span>Translation {latency.translation ?? '—'} ms</span><span>TTS {latency.tts ?? '—'} ms</span><span>Total {latency.total ?? '—'} ms</span></div>}
        </Card>

        <Card className="live-pipeline-card">
          <SectionTitle title="Live pipeline" subtitle="Stages run as a pipeline, not as one long blocking request." />
          <div className="pipeline-visual"><PipelineStep icon={<Mic />} title="Microphone" active={status === 'listening'} /><div className="pipeline-arrow">→</div><PipelineStep icon={<Languages />} title={source === 'sat_Olck' ? 'IndicConformer STT' : 'Whisper STT'} active={status === 'listening'} /><div className="pipeline-arrow">→</div><PipelineStep icon={<AudioLines />} title="IndicTrans2" active={status === 'listening'} /><div className="pipeline-arrow">→</div><PipelineStep icon={<Headphones />} title="Parler-TTS" active={playing} /></div>
          <div className="live-note"><strong>Why this feels faster</strong><p>Speech chunks are bounded, queues stay short, translation models stay warm, repeated phrases are cached in memory, and microphone capture never waits for audio playback.</p></div>
        </Card>
      </div>

      <div className="live-output-grid">
        <Card><SectionTitle title="What was heard" subtitle={`Whisper · ${nativeLabel(source)}`} /><div className="live-text-box">{transcript || <span className="muted">Speak into the microphone to see the live transcript.</span>}</div></Card>
        <Card><SectionTitle title="What the listener hears" subtitle={`IndicTrans2 → Parler-TTS · ${nativeLabel(target)}`} /><div className="live-text-box translated">{translation || <span className="muted">Your translated speech will appear here and play in your selected output.</span>}</div><div className="live-audio-status">{audioGenerating ? <Status tone="info"><RefreshCw size={14} className="spin" /> Generating live audio…</Status> : audioReady ? <Status tone="success"><Volume2 size={14} /> Live audio generated</Status> : translation ? <Status tone="warning"><Headphones size={14} /> Waiting for audio</Status> : null}{playing && <Status tone="success"><Play size={14} /> Playing translated audio</Status>}</div></Card>
      </div>

      <Card className="live-history-card"><SectionTitle title="Session history" subtitle="Live sessions are temporary by default. Unsaved sessions expire after 24 hours; saved sessions remain until you delete them." action={<Status tone="info"><History size={14} /> Device-only history</Status>} />
        {turns.length > 0 && <div className="current-session-bar"><div><strong>Current session</strong><span>{turns.length} completed translation{turns.length === 1 ? '' : 's'}</span></div><div className="button-row"><Button variant="secondary" onClick={() => void saveCurrent()} disabled={!currentSessionId}><Save size={15} /> Save session</Button><Button variant="ghost" onClick={() => void persistCurrentSession()}><RefreshCw size={15} /> Save temporary</Button></div></div>}
        {history.length === 0 ? <div className="empty-live-history"><History size={28} /><strong>No saved or temporary sessions yet.</strong><span>Your current session will appear here after the first translated turn.</span></div> : <div className="history-list">{history.slice(0, 8).map((entry) => <div className={`history-card ${selectedHistory === entry.id ? 'selected' : ''}`} key={entry.id}><button className="history-main" onClick={() => setSelectedHistory(selectedHistory === entry.id ? null : entry.id)}><div><strong>{nativeLabel(entry.source)} → {nativeLabel(entry.target)}</strong><span>{new Date(entry.startedAt).toLocaleString()} · {entry.turns.length} turn{entry.turns.length === 1 ? '' : 's'}</span></div><Status tone={entry.saved ? 'success' : 'warning'}>{entry.saved ? 'Saved' : 'Temporary'}</Status></button><div className="history-actions"><button onClick={() => void setLiveSaved(entry.id, !entry.saved).then(refreshHistory)}>{entry.saved ? 'Make temporary' : 'Save'}</button><button onClick={() => void deleteHistory(entry.id)}><Trash2 size={14} /> Delete</button></div>{selectedHistory === entry.id && <div className="history-turns">{entry.turns.map((turn) => <div className="history-turn" key={turn.id}><div><strong>{turn.transcript}</strong><span>{turn.translation}</span></div>{turn.audio ? <Button variant="secondary" onClick={() => void replay(turn)} title="Hear this translation"><Play size={14} /> Hear audio</Button> : <Status tone="warning">Audio not available for this turn</Status>}</div>)}</div>}</div>)}</div>}
      </Card>
    </Page>
  </>;
}

function PipelineStep({ icon, title, active }: { icon: ReactNode; title: string; active: boolean }) {
  return <div className={`pipeline-step ${active ? 'active' : ''}`}><div className="pipeline-icon">{icon}</div><strong>{title}</strong></div>;
}
