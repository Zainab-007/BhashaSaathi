import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft, ArrowRight, Check, FileText, Languages, Layers, Mic, Pause, Play, Printer,
  RefreshCw, Search, Send, ShieldCheck, Upload, Volume2, XCircle,
} from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';
import { Page, SectionTitle } from '../components/Shell';
import { Button, Busy, Card, Field, Modal, Status, useToast } from '../components/UI';
import { api, errorMessage } from '../lib/api';
import type { Lang, LessonPack, Practice, Translation, VersionSummary } from '../types';

const STEPS = [
  { key: 'source', label: 'Source', short: 'Input' },
  { key: 'concepts', label: 'Concepts', short: 'Meaning' },
  { key: 'languages', label: 'Languages', short: 'Translate' },
  { key: 'activities', label: 'Activities', short: 'Practice + audio' },
  { key: 'review', label: 'Review', short: 'Publish' },
] as const;

const LANGUAGES: Lang[] = ['sat_Olck', 'hin_Deva', 'mar_Deva', 'eng_Latn'];
const label = (lang: Lang) => ({ sat_Olck: 'Santali', hin_Deva: 'Hindi', mar_Deva: 'Marathi', eng_Latn: 'English' } as Record<Lang, string>)[lang];
const nativeLabel = (lang: Lang) => ({ sat_Olck: 'ᱥᱟᱱᱛᱟᱲᱤ', hin_Deva: 'हिन्दी', mar_Deva: 'मराठी', eng_Latn: 'English' } as Record<Lang, string>)[lang];

type AudioState = 'idle' | 'queued' | 'generating' | 'ready' | 'error';
const isUsableTranslation = (translation: Translation | undefined, language: Lang) => Boolean(translation && (translation.verification_status === 'TEACHER_APPROVED' || ((language === 'sat_Olck' || language === 'mar_Deva') && translation.verification_status === 'TEMPORARY')));

export default function LessonBuilderPage() {
  const { id } = useParams();
  const lessonId = Number(id);
  const navigate = useNavigate();
  const { push } = useToast();

  const [pack, setPack] = useState<LessonPack | null>(null);
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [step, setStep] = useState(0);
  const [title, setTitle] = useState('');
  const [topic, setTopic] = useState('');
  const [source, setSource] = useState('');
  const [sourceLanguage, setSourceLanguage] = useState<Lang>('eng_Latn');
  const [selectedTargets, setSelectedTargets] = useState<Lang[]>([]);
  const [transcript, setTranscript] = useState('');
  const [target, setTarget] = useState<Lang>('hin_Deva');
  const [editedTranslation, setEditedTranslation] = useState('');
  const [backTranslation, setBackTranslation] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [translationBusy, setTranslationBusy] = useState<Partial<Record<Lang, boolean>>>();
  const [publishBusy, setPublishBusy] = useState(false);
  const [preparationKick, setPreparationKick] = useState(0);
  const [recording, setRecording] = useState(false);
  const [media, setMedia] = useState<MediaRecorder | null>(null);
  const [artifactUrls, setArtifactUrls] = useState<Record<number, string>>({});
  const [audioStatus, setAudioStatus] = useState<Record<string, AudioState>>({});
  const [audioErrors, setAudioErrors] = useState<Record<string, string>>({});
  const [worksheetHtml, setWorksheetHtml] = useState('');
  const [worksheetOpen, setWorksheetOpen] = useState(false);
  const [lastSaved, setLastSaved] = useState<number | null>(null);
  const [activePracticeLanguage, setActivePracticeLanguage] = useState<Lang>('eng_Latn');
  const [concepts, setConcepts] = useState<string[]>([]);

  const autosaveTimer = useRef<number | null>(null);
  const hydrated = useRef(false);
  const worksheetFrame = useRef<HTMLIFrameElement>(null);
  const warmStarted = useRef(false);
  const preparationSignature = useRef('');
  const publishLock = useRef(false);

  const refresh = useCallback(async () => {
    const current = await api.lesson(lessonId);
    setPack(current);
    setTitle(current.lesson.title);
    setTopic(current.lesson.topic || '');
    setSource(current.version.source_text || '');
    const nextSourceLanguage = current.lesson.source_language;
    setSourceLanguage(nextSourceLanguage);
    try {
      const saved = JSON.parse(localStorage.getItem(`bhashasaathi:v25:lesson-targets:${lessonId}`) || 'null');
      if (Array.isArray(saved)) {
        setSelectedTargets(saved.filter((value): value is Lang => LANGUAGES.includes(value) && value !== nextSourceLanguage));
      } else {
        // V25 starts source-only. Optional target languages are explicit choices,
        // so the teacher never waits for Hindi/Marathi/Santali unless selected.
        setSelectedTargets([]);
      }
    } catch {
      setSelectedTargets([]);
    }
    setTranscript(current.version.clean_transcript || '');
    if (current.version.concepts.length) {
      setConcepts(current.version.concepts.map((concept) => concept.label));
    }
    hydrated.current = true;
    return current;
  }, [lessonId]);


  const loadVersions = useCallback(async () => {
    try { setVersions(await api.versions(lessonId)); } catch { /* diagnostics only */ }
  }, [lessonId]);

  useEffect(() => {
    void (async () => {
      try {
        await refresh();
        await loadVersions();
        setError('');
      } catch (err) {
        setError(errorMessage(err));
      }
    })();
  }, [refresh, loadVersions]);

  const targetLanguages = useMemo(
    () => selectedTargets.filter((language) => language !== sourceLanguage && LANGUAGES.includes(language)),
    [selectedTargets, sourceLanguage],
  );

  const contentLanguages = useMemo(
    () => [sourceLanguage, ...targetLanguages.filter((language) => language !== sourceLanguage)],
    [sourceLanguage, targetLanguages],
  );

  const anyTranslationBusy = targetLanguages.some((language) => Boolean(translationBusy?.[language]));

  useEffect(() => {
    if (target === sourceLanguage) setTarget(targetLanguages[0] || sourceLanguage);
    if (!contentLanguages.includes(activePracticeLanguage)) setActivePracticeLanguage(contentLanguages[0] || sourceLanguage);
  }, [sourceLanguage, targetLanguages, contentLanguages, target, activePracticeLanguage]);

  const currentTranslation = useMemo(
    () => pack?.translations.find((item) => item.language === target),
    [pack, target],
  );

  const translationByLanguage = useMemo(() => {
    const map = new Map<Lang, Translation>();
    pack?.translations.forEach((item) => map.set(item.language, item));
    return map;
  }, [pack]);

  useEffect(() => {
    setEditedTranslation(currentTranslation?.text || '');
    const savedBack = currentTranslation?.validation?.back_translation;
    setBackTranslation(typeof savedBack === 'string' ? savedBack : '');
  }, [currentTranslation?.id, currentTranslation?.text, currentTranslation?.validation?.back_translation]);

  useEffect(() => () => {
    Object.values(artifactUrls).forEach((url) => URL.revokeObjectURL(url));
  }, []);

  // Pre-fetch blob URLs for all ready audio artifacts so audio player is immediately playable
  useEffect(() => {
    if (!pack?.artifacts) return;
    const audioArtifacts = pack.artifacts.filter((item) => item.type === 'audio');
    audioArtifacts.forEach((item) => {
      if (!artifactUrls[item.id]) {
        api.fetchArtifact(item.id)
          .then((blob) => {
            const url = URL.createObjectURL(blob);
            setArtifactUrls((old) => ({ ...old, [item.id]: url }));
          })
          .catch(() => undefined);
      }
    });
  }, [pack?.artifacts, artifactUrls]);


  useEffect(() => {
    if (!hydrated.current) return;
    // Every target is optional. Changing the source language never silently
    // adds English (or any other target), so the teacher never gets an
    // unexpected generation wait.
    setSelectedTargets((items) => items.filter((language) => language !== sourceLanguage));
  }, [sourceLanguage]);

  useEffect(() => {
    if (!hydrated.current) return;
    try { localStorage.setItem(`bhashasaathi:v25:lesson-targets:${lessonId}`, JSON.stringify(selectedTargets)); } catch { /* local preference only */ }
  }, [lessonId, selectedTargets]);

  useEffect(() => { warmStarted.current = false; }, [sourceLanguage]);

  // Warm translation routes only after the editor has been idle for a moment.
  // This avoids competing with the teacher's first click on Generate targets.
  useEffect(() => {
    if (!hydrated.current || warmStarted.current || busy || targetLanguages.some((language) => translationBusy?.[language])) return;
    const timer = window.setTimeout(() => {
      if (warmStarted.current) return;
      warmStarted.current = true;
      void (async () => {
        for (const language of targetLanguages) {
          try { await api.warmTranslation(sourceLanguage, language); } catch { /* best effort */ }
        }
      })();
    }, 1400);
    return () => window.clearTimeout(timer);
  }, [sourceLanguage, targetLanguages, busy, translationBusy]);

  const saveDraft = useCallback(async (silent = false) => {
    if (!hydrated.current || publishLock.current) return;
    try {
      const updated = await api.patchLesson(lessonId, {
        title: title.trim(),
        topic: topic.trim(),
        source_text: source,
        clean_transcript: transcript,
        concepts,
        source_language: sourceLanguage,
      });
      setPack(updated);
      setLastSaved(Date.now());
      if (!silent) push('Draft saved.');
    } catch (err) {
      if (!silent) setError(errorMessage(err));
    }
  }, [lessonId, title, topic, source, sourceLanguage, transcript, concepts, push]);

  useEffect(() => {
    if (!hydrated.current) return;
    if (autosaveTimer.current) window.clearTimeout(autosaveTimer.current);
    autosaveTimer.current = window.setTimeout(() => void saveDraft(true), 1800);
    return () => { if (autosaveTimer.current) window.clearTimeout(autosaveTimer.current); };
  }, [title, topic, source, transcript, concepts, saveDraft]);

  const run = async (labelText: string, action: () => Promise<void>) => {
    setBusy(labelText);
    setError('');
    try { await action(); }
    catch (err) { setError(errorMessage(err)); }
    finally { setBusy(''); }
  };

  const prepareConcepts = async (): Promise<boolean> => {
    setBusy('Preparing concepts…');
    setError('');
    try {
      await saveDraft(true);
      const result = await api.prepareConcepts(lessonId);
      const nextConcepts = (result.concepts || []).map((item: any) => String(item.label || '')).filter(Boolean);
      setConcepts(nextConcepts);
      await refresh();
      push('Concepts prepared.');
      return nextConcepts.length > 0;
    } catch (err) {
      setError(errorMessage(err));
      return false;
    } finally {
      setBusy('');
    }
  };

  const applyTranslationResult = useCallback((language: Lang, result: any, selectLanguage = true) => {
    setPack((previous) => {
      if (!previous) return previous;
      const nextTranslation: Translation = {
        id: Number(result.translation_id),
        language,
        text: String(result.text || ''),
        confidence: Number(result.confidence || 0),
        flags: Array.isArray(result.flags) ? result.flags : [],
        validation: result.validation || {},
        verification_status: String(result.verification_status || 'UNVERIFIED'),
        teacher_edited: false,
        native_review_status: String(result.native_review_status || 'NOT_REVIEWED'),
        student_feedback: result.student_feedback || { confirmed: 0, reported: 0 },
        my_student_feedback: null,
      };
      return {
        ...previous,
        translations: [...previous.translations.filter((item) => item.language !== language), nextTranslation],
        artifacts: previous.artifacts.filter((item) => !(item.type === 'audio' && item.language === language)),
      };
    });
    if (selectLanguage) setEditedTranslation(String(result.text || ''));
    if (selectLanguage) setBackTranslation('');
  }, []);

  const translateOne = async (language: Lang) => {
    if (busy || translationBusy?.[language]) return;
    if (language === sourceLanguage) {
      setError('Choose a target language different from the lesson source.');
      return;
    }
    setError('');
    setTranslationBusy((old) => ({ ...old, [language]: true }));
    try {
      const result = await api.translate(lessonId, language, source, transcript, sourceLanguage);
      applyTranslationResult(language, result);
      setTarget(language);
      push(`${label(language)} ready for review (${Math.round(Number(result.total_ms || result.translation_ms || 0))} ms model path).`);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setTranslationBusy((old) => ({ ...old, [language]: false }));
    }
  };

  const translateAll = async () => {
    if (busy || targetLanguages.some((language) => translationBusy?.[language])) return;
    setError('');
    const targets = targetLanguages.filter((language) => {
      const existing = translationByLanguage.get(language);
      return !(existing?.verification_status === 'TEACHER_APPROVED' && existing.text.trim());
    });
    if (!targets.length) {
      push(targetLanguages.length ? 'Selected target translations are already prepared/approved.' : 'No optional target languages are selected.');
      return;
    }
    setTarget(targets[0] || sourceLanguage);
    setTranslationBusy((old) => ({ ...old, ...Object.fromEntries(targets.map((language) => [language, true])) }));
    const failures: string[] = [];
    try {
      const results = await Promise.allSettled(targets.map(async (language) => {
        const result = await api.translate(lessonId, language, source, transcript, sourceLanguage);
        applyTranslationResult(language, result, false);
        push(`${label(language)} translation is ready to review.`);
        return language;
      }));
      results.forEach((result, index) => {
        if (result.status === 'rejected') failures.push(`${label(targets[index])}: ${errorMessage(result.reason)}`);
      });
      const firstReady = results.find((result) => result.status === 'fulfilled') as PromiseFulfilledResult<Lang> | undefined;
      if (firstReady) setTarget(firstReady.value);
      if (failures.length) setError(`Some translations could not be prepared. ${failures.join(' • ')}`);
      else push('Target translations are ready. Review and approve each one.');
    } finally {
      setTranslationBusy((old) => ({ ...old, ...Object.fromEntries(targets.map((language) => [language, false])) }));
    }
  };

  const meaningCheck = () => run(`Checking ${label(target)} meaning…`, async () => {
    if (!currentTranslation) throw new Error('Generate the translation first.');
    const result = await api.backTranslate(lessonId, currentTranslation.id);
    setBackTranslation(result.back_translation || '');
    push('Meaning check ready. Compare it with the teacher source.');
  });

  const saveTemporaryTarget = () => run(`Saving temporary ${label(target)} text…`, async () => {
    if (target !== 'sat_Olck' && target !== 'mar_Deva') throw new Error('Temporary save is only available for Santali and Marathi.');
    if (!currentTranslation) throw new Error(`Generate ${label(target)} before saving it.`);
    if (!editedTranslation.trim()) throw new Error(`${label(target)} text cannot be empty.`);
    const changed = currentTranslation.text.trim() !== editedTranslation.trim() || currentTranslation.verification_status !== 'TEMPORARY';
    if (changed) {
      const updated = await api.saveTemporaryTranslation(lessonId, {
        translation_id: currentTranslation.id,
        edited_text: editedTranslation,
      });
      setPack((previous) => previous ? {
        ...previous,
        translations: previous.translations.map((item) => item.id === currentTranslation.id ? {
          ...item,
          text: String(updated.text || editedTranslation),
          verification_status: 'TEMPORARY',
          native_review_status: 'NOT_REQUIRED',
          teacher_edited: item.text.trim() !== editedTranslation.trim() || item.teacher_edited,
        } : item),
      } : previous);
    }
    setPreparationKick((value) => value + 1);
    const requiredTargets = targetLanguages.filter((language) => language !== 'sat_Olck' && language !== 'mar_Deva');
    const requiredReady = requiredTargets.every((language) => isUsableTranslation(translationByLanguage.get(language), language));
    if (requiredReady) setStep(3);
    push(`${label(target)} is saved as a temporary language version. No approval is required.`);
  });

  const saveTranslation = () => run('Saving text approval…', async () => {
    if (!currentTranslation) throw new Error(`Generate ${label(target)} before approving it.`);
    const result = await api.verify(lessonId, {
      translation_id: currentTranslation.id,
      approved: true,
      edited_text: editedTranslation,
      native_review_status: target === 'mar_Deva' ? 'PENDING' : 'NOT_REVIEWED',
    });
    setPack((previous) => previous ? {
      ...previous,
      translations: previous.translations.map((item) => item.id === currentTranslation.id ? {
        ...item,
        text: String(result.text || editedTranslation),
        verification_status: 'TEACHER_APPROVED',
        teacher_edited: item.text.trim() !== editedTranslation.trim() || item.teacher_edited,
      } : item),
    } : previous);
    setPreparationKick((value) => value + 1);
    const approvalTargets = targetLanguages.filter((language) => language !== 'sat_Olck');
    const allApprovedAfter = approvalTargets.every((language) => (
      language === target || translationByLanguage.get(language)?.verification_status === 'TEACHER_APPROVED'
    ));
    if (allApprovedAfter) {
      push(`${label(target)} approved. All required languages are approved; activities are now available.`);
      setStep(3);
    } else {
      const nextUnapproved = approvalTargets.find((language) => (
        language !== target && translationByLanguage.get(language)?.verification_status !== 'TEACHER_APPROVED'
      ));
      if (nextUnapproved) setTarget(nextUnapproved);
      push(`${label(target)} approved. Review and approve the remaining target language.`);
    }
  });

  const generatePractice = (language: Lang) => run(`Preparing ${label(language)} practice…`, async () => {
    await api.practice(lessonId, language, 4);
    await refresh();
    setActivePracticeLanguage(language);
    push(`${label(language)} practice is ready for review.`);
  });

  const approvePractice = (question: Practice) => run('Saving practice approval…', async () => {
    await api.approvePractice(lessonId, question.id, true);
    await refresh();
    push('Practice question approved.');
  });

  const generateWorksheet = () => run('Preparing worksheet…', async () => {
    const result = await api.worksheet(lessonId);
    setWorksheetHtml(result.html);
    setWorksheetOpen(true);
    push('Worksheet preview is ready.');
  });


  const queueAudio = (language: Lang) => run(`Queueing ${label(language)} audio…`, async () => {
    const result = await api.queueTTSPreparation(lessonId, language);
    const nextState: AudioState = result.status === 'READY' ? 'ready' : result.status === 'RUNNING' ? 'generating' : result.status === 'FAILED' ? 'error' : 'queued';
    setAudioStatus((old) => ({ ...old, [language]: nextState }));
    setAudioErrors((old) => ({ ...old, [language]: result.error || '' }));
    setPreparationKick((value) => value + 1);
    if (nextState === 'ready') {
      await refresh();
      push(`${label(language)} audio is already ready.`);
    } else {
      push(`${label(language)} audio queued. It is separate from publishing and practice.`);
    }
  });

  const playArtifact = useCallback(async (language: Lang) => {
    const artifact = pack?.artifacts.find((item) => item.type === 'audio' && item.language === language);
    if (!artifact) { push(`${label(language)} audio is still preparing.`); return; }
    let url = artifactUrls[artifact.id];
    if (!url) {
      const blob = await api.fetchArtifact(artifact.id);
      url = URL.createObjectURL(blob);
      setArtifactUrls((old) => ({ ...old, [artifact.id]: url! }));
    }
    const audio = document.getElementById(`lesson-audio-${language}`) as HTMLAudioElement | null;
    if (audio) {
      audio.src = url;
      await audio.play().catch(() => undefined);
    }
  }, [pack?.artifacts, artifactUrls, push]);

  // Background preparation status is persisted in the database. Poll only while
  // work exists, and refresh the full lesson pack only when a job completes.
  const syncPreparations = useCallback(async () => {
    try {
      const jobs = await api.preparations(lessonId);
      setAudioStatus((old) => {
        const next = { ...old };
        for (const job of jobs) {
          if (job.kind === 'tts' && job.language) {
            next[job.language] = job.status === 'FAILED' ? 'error' : job.status === 'READY' ? 'ready' : job.status === 'RUNNING' ? 'generating' : 'queued';
          }
        }
        return next;
      });
      setAudioErrors((old) => {
        const next = { ...old };
        for (const job of jobs) {
          if (job.kind === 'tts' && job.language) next[job.language] = job.error || '';
        }
        return next;
      });
      const signature = jobs.map((job) => `${job.id}:${job.status}:${job.error || ''}`).join('|');
      const previous = preparationSignature.current;
      preparationSignature.current = signature;
      const terminalChanged = jobs.some((job) => {
        if (job.status !== 'READY' && job.status !== 'FAILED') return false;
        return !previous.split('|').some((item) => item.startsWith(`${job.id}:${job.status}:`));
      });
      if (terminalChanged) await refresh();
      return jobs.some((job) => job.status === 'QUEUED' || job.status === 'RUNNING');
    } catch {
      return false;
    }
  }, [lessonId, refresh]);

  useEffect(() => {
    let timer: number | null = null;
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      const active = await syncPreparations();
      if (!cancelled && active) timer = window.setTimeout(tick, 1600);
    };
    void tick();
    return () => { cancelled = true; if (timer) window.clearTimeout(timer); };
  }, [syncPreparations, preparationKick]);

  const createVersion = () => run('Saving new version…', async () => {
    const updated = await api.newVersion(lessonId, { source_text: source, clean_transcript: transcript, concepts });
    setPack(updated);
    await loadVersions();
    setStep(0);
    push(`Version ${updated.version.version_number} created.`);
  });

  const rollback = (versionId: number) => run('Restoring version…', async () => {
    const updated = await api.rollback(lessonId, versionId);
    setPack(updated);
    await loadVersions();
    push(`Published version ${updated.version.version_number} restored.`);
  });

  const approvedPractice = (pack?.practice_all || []).filter((question) => question.approved);
  const practiceByLanguage = new Map<Lang, Practice[]>();
  for (const language of contentLanguages) {
    practiceByLanguage.set(language, approvedPractice.filter((question) => question.language === language));
  }
  const practiceReady = contentLanguages.some((language) => (practiceByLanguage.get(language)?.length || 0) > 0);
  const practiceDrafts = (pack?.practice_all || []).filter((question) => !question.approved);
  const audioReadyByLanguage = contentLanguages.map((language) => ({
    language,
    ready: Boolean(pack?.artifacts.some((artifact) => artifact.type === 'audio' && artifact.language === language)),
  }));
  const audioReady = audioReadyByLanguage.every((item) => item.ready);

  const readiness = useMemo(() => {
    const problems: string[] = [];
    if (!title.trim()) problems.push('Add a title');
    if (!source.trim() && !transcript.trim()) problems.push('Add lesson source');
    return { publish: problems.length === 0, problems };
  }, [title, source, transcript]);

  const doPublish = async () => {
    if (!readiness.publish) { setError(`Publish is blocked: ${readiness.problems.join(' • ')}`); return; }
    if (publishBusy) return;
    setPublishBusy(true);
    setError('');
    publishLock.current = true;
    if (autosaveTimer.current) {
      window.clearTimeout(autosaveTimer.current);
      autosaveTimer.current = null;
    }
    try {
      const updated = await api.publish(lessonId);
      setPack(updated);
      await loadVersions();
      push('Lesson published. Students can now download it for offline learning.');
    } catch (err) {
      publishLock.current = false;
      setError(errorMessage(err));
    } finally {
      setPublishBusy(false);
    }
  };

  const next = async () => {
    if (step === 0) {
      if (!title.trim() || !(source.trim() || transcript.trim())) {
        setError('Complete the lesson title and add the teacher source first.');
        return;
      }
      if (!concepts.length) {
        const prepared = await prepareConcepts();
        if (!prepared) return;
      }
    }
    if (step === 1 && !concepts.length) {
      setError('Prepare at least one concept.');
      return;
    }
    setError('');
    setStep((current) => Math.min(STEPS.length - 1, current + 1));
  };

  const back = () => setStep((current) => Math.max(0, current - 1));

  const record = async () => {
    if (recording) {
      media?.stop();
      setRecording(false);
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setError('This browser cannot record audio. Use the text editor or upload an audio file.');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      const chunks: BlobPart[] = [];
      recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
        const extension = blob.type.includes('ogg') ? 'ogg' : 'webm';
        const file = new File([blob], `teacher-recording.${extension}`, { type: blob.type });
        void run('Transcribing audio…', async () => {
          const result = await api.transcribe(lessonId, file);
          setTranscript(result.text || '');
          setSource(result.text || '');
          push('Transcript prepared. Edit it before continuing.');
        });
      };
      setMedia(recorder);
      recorder.start();
      setRecording(true);
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  const uploadAudio = (file?: File) => {
    if (!file) return;
    void run('Transcribing audio…', async () => {
      const result = await api.transcribe(lessonId, file);
      setTranscript(result.text || '');
      setSource(result.text || '');
      await refresh();
      push('Transcript prepared. Edit it before continuing.');
    });
  };

  if (!pack) {
    return <Page title="Opening lesson…"><Card>{error ? <div className="stack-sm"><Status tone="danger">{error}</Status><Button onClick={() => { setError(''); void refresh(); }}><RefreshCw size={15} /> Retry</Button></div> : <Busy label="Loading lesson workspace…" />}</Card></Page>;
  }

  return <>
    <Page
      eyebrow={`${pack.lesson.grade} · ${pack.lesson.subject}`}
      title={pack.lesson.title}
      subtitle={`${label(sourceLanguage)} is the source language. Optional target languages (Hindi, Marathi, temporary Santali) never block publishing.`}
      actions={<div className="head-action-row"><Button variant="ghost" onClick={() => navigate(`/groups/${pack.lesson.group_id}`)}><ArrowLeft size={16} /> Classroom</Button>{lastSaved && <Status tone="success">Saved {new Date(lastSaved).toLocaleTimeString()}</Status>}<Status tone={readiness.publish ? 'success' : 'warning'}>{readiness.publish ? 'Ready' : 'Draft'}</Status></div>}
    >
      {(busy || anyTranslationBusy) && <div className="busy-bar"><Busy label={busy || `Preparing ${targetLanguages.filter((language) => translationBusy?.[language]).map(label).join(' + ')} translations…`} /></div>}
      {error && <div className="error-banner"><XCircle size={18} /><div><strong>We couldn't complete that step.</strong><span>{error}</span></div><button onClick={() => setError('')} aria-label="Dismiss"><XCircle size={15} /></button></div>}

      <div className="builder-steps">
        {STEPS.map((item, index) => {
          const available = index <= step;
          const isBlocked = !available && index > step;
          return <button key={item.key} disabled={isBlocked} title={isBlocked ? 'Complete the previous step first.' : undefined} className={index === step ? 'active' : available ? 'done' : ''} onClick={() => available ? setStep(index) : setError(`Finish the earlier step before opening ${item.label}.`)}>
            <span className="step-number">{available && index !== step ? <Check size={15} /> : index + 1}</span><div><strong>{item.label}</strong><small>{item.short}</small></div>
          </button>;
        })}
      </div>

      <div className="builder-layout">
        <section className="builder-main">
          {step === 0 && <Card><SectionTitle title="1. Teacher source" subtitle="Write or record the lesson. The original source is preserved." />
            <div className="form-grid three"><Field label="Lesson title"><input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Why Plants Need Water" /></Field><Field label="Source language"><select value={sourceLanguage} disabled={anyTranslationBusy || !!busy} onChange={(e) => setSourceLanguage(e.target.value as Lang)}><option value="eng_Latn">English · मुख्य</option><option value="hin_Deva">हिन्दी · Hindi</option><option value="mar_Deva">मराठी · Marathi</option><option value="sat_Olck">ᱥᱟᱱᱛᱟᱲᱤ · Santali</option></select></Field><Field label="Topic"><input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="Plants" /></Field></div>
            <Field label="Teacher lesson"><textarea rows={12} value={source} disabled={anyTranslationBusy || !!busy} onChange={(e) => { setSource(e.target.value); setTranscript(e.target.value); }} placeholder="Today we will learn why plants need water…" /></Field>
            <div className="source-tools"><Button variant={recording ? 'danger' : 'secondary'} onClick={() => void record()}>{recording ? <Pause size={16} /> : <Mic size={16} />}{recording ? 'Stop & transcribe' : `Record ${label(sourceLanguage)}`}</Button><label className="upload-button"><Upload size={16} /> Upload audio<input type="file" accept="audio/*" hidden onChange={(e) => uploadAudio(e.target.files?.[0])} /></label><Button variant="ghost" onClick={() => void saveDraft(false)} disabled={!!busy}>Save draft</Button></div>
            <div className="privacy-note"><ShieldCheck size={16} /><span>Source text stays immutable in the version history; generated translations never overwrite it.</span></div>
            <div className="wizard-nav"><span /><Button onClick={() => void next()}>Continue <ArrowRight size={16} /></Button></div>
          </Card>}

          {step === 1 && <Card><SectionTitle title="2. Concepts" subtitle="Keep only the concepts that matter. No learning-outcome generation is required." />
            <div className="analysis-panel"><div><span className="eyebrow">SOURCE</span><p>{transcript || source}</p></div></div>
            <div className="button-row"><Button onClick={() => void prepareConcepts()} disabled={!!busy || !(source.trim() || transcript.trim())}><RefreshCw size={16} /> Auto-find concepts</Button><Status tone="info">Local, deterministic extraction</Status></div>
            <div className="concept-editor"><div className="concept-editor-head"><strong>Key concepts</strong><Button variant="ghost" onClick={() => setConcepts((items) => [...items, ''])}><FileText size={15} /> Add concept</Button></div>{concepts.map((concept, index) => <div className="concept-edit-row" key={`concept-${index}`}><span>{index + 1}</span><input value={concept} onChange={(e) => setConcepts((items) => items.map((item, itemIndex) => itemIndex === index ? e.target.value : item))} placeholder="Concept" /><button onClick={() => setConcepts((items) => items.filter((_, itemIndex) => itemIndex !== index))} aria-label="Remove concept">×</button></div>)}</div>
            <div className="info-banner"><ShieldCheck size={16} /><div><strong>Speed rule</strong><p>Concept extraction is local and lightweight. Heavy translation/TTS models do not run here.</p></div></div>
            <div className="wizard-nav"><Button variant="ghost" onClick={back}><ArrowLeft size={16} /> Back</Button><Button variant="secondary" onClick={() => void saveDraft(false)}>Save concepts</Button><Button onClick={() => void next()}>Continue <ArrowRight size={16} /></Button></div>
          </Card>}

          {step === 2 && <Card><SectionTitle title="3. Languages & Translation Guard" subtitle="Santali is the main target language. Hindi and Marathi are optional and never block publishing." />
            <div className="subcard" style={{ marginTop: 0 }}><div className="subcard-head"><div><strong>Target languages (Santali is Main, Hindi/Marathi Optional)</strong><small style={{ display: 'block', marginTop: 4, color: '#718079' }}>Santali is the primary classroom target. Hindi is optional. Marathi is temporary optional. Translations never block publishing.</small></div><Status tone="info">{targetLanguages.length ? `${targetLanguages.length} selected` : 'Source only'}</Status></div><div className="button-row wrap">{LANGUAGES.filter((lang) => lang !== sourceLanguage).map((lang) => { const selected = targetLanguages.includes(lang); return <button key={lang} type="button" aria-pressed={selected} className={selected ? 'language-tab active' : 'language-tab'} onClick={() => { setSelectedTargets((items) => selected ? items.filter((item) => item !== lang) : [...items, lang]); if (!selected) setTarget(lang); }}>{nativeLabel(lang)} · {label(lang)}{lang === 'sat_Olck' ? ' (Main)' : lang === 'mar_Deva' ? ' (Temporary)' : ' (Optional)'}</button>; })}</div></div>
            <div className="language-toolbar"><Button onClick={() => void translateAll()} disabled={!!busy || targetLanguages.some((language) => Boolean(translationBusy?.[language])) || !(source.trim() || transcript.trim()) || !targetLanguages.length}><RefreshCw size={16} /> Generate selected</Button>{targetLanguages.map((lang) => <button key={lang} className={target === lang ? 'language-tab active' : 'language-tab'} onClick={() => setTarget(lang)}>{nativeLabel(lang)}{translationBusy?.[lang] ? ' · generating' : ''}</button>)}{!targetLanguages.length && <Status tone="info">You can publish in the original language without translations.</Status>}</div>
            {targetLanguages.length ? <><div className="translation-grid-large"><div className="translation-source"><div className="translation-head"><strong>Teacher source</strong><Status>{nativeLabel(sourceLanguage)} · Source</Status></div><div className="translation-text original">{transcript || source}</div></div><div className="translation-target"><div className="translation-head"><strong>{nativeLabel(target)}</strong>{currentTranslation ? <Status tone={currentTranslation.verification_status === 'TEACHER_APPROVED' || currentTranslation.verification_status === 'TEMPORARY' ? 'success' : 'warning'}>{currentTranslation.verification_status === 'TEMPORARY' ? 'TEMPORARY' : currentTranslation.verification_status.replaceAll('_', ' ')}</Status> : <Status>Not prepared</Status>}</div><textarea rows={15} value={editedTranslation} onChange={(e) => setEditedTranslation(e.target.value)} placeholder={`Generate ${label(target)} to review it here…`} /></div></div>
            <div className="translation-actions"><Button variant="secondary" onClick={() => void translateOne(target)} disabled={!!busy || !!translationBusy?.[target]}><RefreshCw size={15} /> {translationBusy?.[target] ? `Generating ${label(target)}…` : `Generate ${label(target)}`}</Button>{currentTranslation && <Button variant="secondary" onClick={() => void meaningCheck()} disabled={!!busy || !!translationBusy?.[target]}><Search size={15} /> Meaning Check</Button>}{currentTranslation && (target === 'sat_Olck' || target === 'mar_Deva') ? <Button onClick={() => void saveTemporaryTarget()} disabled={!!busy || !!translationBusy?.[target] || !editedTranslation.trim() || (currentTranslation.verification_status === 'TEMPORARY' && currentTranslation.text.trim() === editedTranslation.trim())}><Check size={15} /> {currentTranslation.verification_status === 'TEMPORARY' ? `Save ${label(target)} changes` : 'Save as temporary'}</Button> : currentTranslation && <Button onClick={() => void saveTranslation()} disabled={!!busy || !!translationBusy?.[target] || !editedTranslation.trim() || currentTranslation.verification_status === 'TEACHER_APPROVED'}><Check size={15} /> Approve {label(target)}</Button>}{currentTranslation && currentTranslation.flags.length > 0 ? <Status tone="warning">{currentTranslation.flags.length} Guard flag(s)</Status> : currentTranslation && currentTranslation.verification_status === 'TEMPORARY' ? <Status tone="info">Temporary {label(target)} — approval is not required</Status> : currentTranslation ? <Status tone="success">Automated checks passed; teacher review still required</Status> : <Status tone="warning">This language is not prepared yet</Status>}</div>
            </> : null}
            {!targetLanguages.length && <div className="info-banner"><Languages size={16} /><div><strong>Source-only lesson</strong><p>{label(sourceLanguage)} is the source language. Select Hindi, Marathi, or temporary Santali whenever you want an optional version. Publishing can continue without any translation.</p></div></div>}
            {currentTranslation && <div className="guard-panel-large"><div className="guard-summary"><div><span>Confidence</span><strong>{Math.round(currentTranslation.confidence * 100)}%</strong></div><div><span>Concepts missing</span><strong>{currentTranslation.validation?.concepts_missing?.length || 0}</strong></div><div><span>Numbers</span><strong>{currentTranslation.validation?.number_mismatch ? 'Mismatch' : 'OK'}</strong></div><div><span>Source leakage</span><strong>{currentTranslation.validation?.source_leakage ? 'Review' : 'OK'}</strong></div></div>{currentTranslation.flags.length > 0 && <div className="flag-list">{currentTranslation.flags.map((flag) => <span key={flag}>{flag.replaceAll('_', ' ')}</span>)}</div>}{backTranslation && <div className="info-banner"><Search size={16} /><div><strong>Meaning Check · source-language back translation</strong><p>{backTranslation}</p></div></div>}{currentTranslation.native_review_status === 'PENDING' && <div className="native-review"><div><strong>Optional native-language review</strong><span>Useful when the teacher does not know the target language.</span></div><Status tone="warning">Pending</Status></div>}{(currentTranslation.student_feedback?.confirmed || currentTranslation.student_feedback?.reported) ? <div className="native-review"><div><strong>Student language feedback</strong><span>{currentTranslation.student_feedback?.confirmed || 0} confirmed · {currentTranslation.student_feedback?.reported || 0} reported</span></div><Status tone={(currentTranslation.student_feedback?.reported || 0) > 0 ? 'warning' : 'success'}>{(currentTranslation.student_feedback?.reported || 0) > 0 ? 'Review reports' : 'No reports'}</Status></div> : null}</div>}
            <div className="wizard-nav"><Button variant="ghost" onClick={back}><ArrowLeft size={16} /> Back</Button><Button onClick={() => void next()}>Continue to activities <ArrowRight size={16} /></Button></div>
          </Card>}

          {step === 3 && <Card><SectionTitle title="4. Activities, worksheets & audio" subtitle="Everything here is optional and on-demand. Generate practice or audio only when you need it; nothing here blocks publishing." />
            <div className="action-card-grid">
              <Card className="action-card"><div className="action-icon"><FileText size={19} /></div><strong>Practice</strong><span>Optional. Generate questions only for the language you want. Practice never blocks publishing.</span><div className="button-row wrap">{contentLanguages.map((lang) => <div key={lang} className="button-row"><Button variant={activePracticeLanguage === lang ? 'primary' : 'secondary'} onClick={() => setActivePracticeLanguage(lang)} disabled={!!busy}>{label(lang)}</Button><Button variant="ghost" onClick={() => void generatePractice(lang)} disabled={!!busy}>Generate</Button></div>)}</div></Card>
              <Card className="action-card"><div className="action-icon"><Printer size={19} /></div><strong>Worksheet</strong><span>Generate only when needed. It is not part of the critical publish path.</span><Button variant="secondary" onClick={() => void generateWorksheet()} disabled={!!busy}>Preview worksheet</Button></Card>
              <Card className="action-card">
                <div className="action-icon"><Volume2 size={19} /></div>
                <strong>Audio</strong>
                <span>Audio is manual because local Parler-TTS can be slow. Generate only when needed; Publish never waits for audio.</span>
                <div className="audio-prep-list">
                  {contentLanguages.map((lang) => {
                    const artifact = pack.artifacts.find((item) => item.type === 'audio' && item.language === lang);
                    const state = audioStatus[lang] || (artifact ? 'ready' : 'idle');
                    const job = pack.preparations?.find((item) => item.kind === 'tts' && item.language === lang);
                    const disabled = !!busy;
                    return <div className="audio-prep-row" key={lang}>
                      <div><strong>{label(lang)}</strong><small>{state === 'ready' ? 'Audio ready' : state === 'generating' ? 'Generating in background…' : state === 'queued' ? 'Queued in background…' : state === 'error' ? (audioErrors[lang] || 'Generation failed') : 'Waiting for usable text'}</small></div>
                      <div className="button-row">
                        {state === 'ready' && artifact ? <>
                          <audio id={`lesson-audio-${lang}`} controls preload="metadata" src={artifactUrls[artifact.id]} />
                          <Button variant="secondary" onClick={() => void playArtifact(lang)}><Play size={14} /> Hear</Button>
                        </> : state === 'error' ? <Button variant="secondary" disabled={disabled} onClick={() => {
                          if (!job) { void queueAudio(lang); return; }
                          setAudioStatus((old) => ({ ...old, [lang]: 'queued' }));
                          setAudioErrors((old) => ({ ...old, [lang]: '' }));
                          void run('Retrying audio…', async () => {
                            const result = await api.retryPreparation(lessonId, job.id);
                            const nextState: AudioState = result.status === 'READY' ? 'ready' : result.status === 'RUNNING' ? 'generating' : result.status === 'FAILED' ? 'error' : 'queued';
                            setAudioStatus((old) => ({ ...old, [lang]: nextState }));
                            setAudioErrors((old) => ({ ...old, [lang]: result.error || '' }));
                            setPreparationKick((value) => value + 1);
                            if (nextState === 'ready') await refresh();
                          });
                        }}>Retry</Button> : state === 'idle' ? <Button variant="secondary" disabled={disabled} onClick={() => void queueAudio(lang)}>Generate</Button> : <Status tone="info">Background</Status>}
                      </div>
                    </div>;
                  })}
                </div>
              </Card>
            </div>
            <div className="info-banner"><ShieldCheck size={16} /><div><strong>Publish rule</strong><p>Publishing uses the original source. Optional Hindi, Marathi, temporary Santali, practice, and audio never block publishing.</p></div></div>
            <div className="practice-review-block"><strong>Practice review</strong><span>Background questions are optional for publication. Review and approve them when you want the practice activity live.</span>{practiceDrafts.length > 0 && <div className="practice-list">{practiceDrafts.slice(0, 6).map((question) => <div className="practice-row" key={question.id}><div><strong>{question.prompt}</strong><small>{label(question.language)}</small></div><Button variant="secondary" onClick={() => void approvePractice(question)} disabled={!!busy}><Check size={14} /> Approve</Button></div>)}</div>}{practiceDrafts.length === 0 && <Status tone={practiceReady ? 'success' : 'info'}>{practiceReady ? 'Approved practice is ready.' : 'Generate practice only when you need it. It is never required for publishing.'}</Status>}</div>
            <div className="wizard-nav"><Button variant="ghost" onClick={back}><ArrowLeft size={16} /> Back</Button><Button onClick={() => void next()}>Continue to review <ArrowRight size={16} /></Button></div>
          </Card>}
          {step === 4 && <Card><SectionTitle title="5. Review & publish" subtitle="English/source content is always publishable once the lesson is valid. Optional translations, temporary Santali, practice, and audio are separate assets." />
            <div className="preview-card"><div className="preview-top"><div><span className="eyebrow">STUDENT PREVIEW</span><h2>{title}</h2><p>{topic || 'Multilingual classroom lesson'}</p></div><Status tone={readiness.publish ? 'success' : 'warning'}>{readiness.publish ? 'Ready to publish' : 'Review required'}</Status></div><div className="preview-concepts">{concepts.filter(Boolean).map((concept) => <span key={concept}>{concept}</span>)}</div><div className="preview-source-strip"><strong>{nativeLabel(sourceLanguage)} · Original teacher source</strong><p>{transcript || source}</p></div><div className="preview-language-grid">{targetLanguages.map((lang) => { const translation = translationByLanguage.get(lang); const approved = translation?.verification_status === 'TEACHER_APPROVED'; return <div className="preview-language" key={lang}><div><strong>{nativeLabel(lang)}</strong>{translation ? <Status tone={approved || translation.verification_status === 'TEMPORARY' ? 'success' : 'warning'}>{translation.verification_status === 'TEMPORARY' ? 'TEMPORARY' : translation.verification_status.replaceAll('_', ' ')}</Status> : <Status>Not prepared</Status>}</div><p>{translation?.text || 'Not prepared yet.'}</p></div>; })}</div></div>
            <div className="publish-checklist"><div><Check size={15} /><span>Original source preserved</span></div><div><Check size={15} /><span>{targetLanguages.length ? `Optional ${targetLanguages.map(label).join(' + ')} language versions are stored independently` : 'Original language is the active lesson language'}</span></div><div><Check size={15} /><span>{approvedPractice.length} approved practice question(s)</span></div><div><Check size={15} /><span>Audio is optional and never blocks publishing</span></div><div><Check size={15} /><span>Published package becomes eligible for offline sync</span></div></div>
            {!readiness.publish && <div className="blocker-panel"><strong>Still blocked</strong><ul>{readiness.problems.map((problem) => <li key={problem}>{problem.replaceAll('_',' ')}</li>)}</ul></div>}
            <div className="button-row wrap"><Button variant="secondary" onClick={createVersion} disabled={!!busy}><Layers size={15} /> Save as new version</Button><Button variant="secondary" onClick={() => void generateWorksheet()} disabled={!!busy}><Printer size={15} /> Worksheet preview</Button><Button onClick={() => void doPublish()} disabled={!readiness.publish || publishBusy}><Send size={15} /> {publishBusy ? 'Publishing…' : 'Publish lesson'}</Button></div>
            <div className="version-history"><SectionTitle title="Version history" subtitle="Restore only previously published versions; history is never deleted." />{versions.map((version) => <div className="version-row" key={version.id}><div><strong>Version {version.version_number}</strong><span>{new Date(version.created_at).toLocaleString()}</span></div><div>{version.published_at ? <Status tone="success">Published</Status> : <Status>Draft</Status>}{version.published_at && version.id !== pack.version.id && <Button variant="ghost" onClick={() => void rollback(version.id)} disabled={!!busy}>Restore</Button>}</div></div>)}</div>
          </Card>}
        </section>

        <aside className="builder-side"><Card className="sticky-card"><SectionTitle title="Live readiness" subtitle="Optional preparation is manual and never blocks the lesson flow." /><div className="status-list"><Readiness label="Original lesson" ok={Boolean(source.trim() || transcript.trim())} text={source.trim() || transcript.trim() ? 'Ready' : 'Missing'} /><Readiness label="Concepts" ok={Boolean(concepts.filter(Boolean).length)} text={`${concepts.filter(Boolean).length} saved`} />{targetLanguages.map((language) => { const translation = translationByLanguage.get(language); const usable = isUsableTranslation(translation, language); return <Readiness key={language} label={label(language)} ok={usable} text={translation ? ((language === 'sat_Olck' || language === 'mar_Deva') && translation.verification_status === 'TEMPORARY' ? 'Temporary' : translation.verification_status === 'TEACHER_APPROVED' ? 'Approved' : 'Review') : 'Not prepared'} />; })}<Readiness label="Practice" ok={practiceReady} text={practiceReady ? 'Ready' : 'Background'} />{audioReadyByLanguage.map(({ language, ready }) => <Readiness key={`audio-${language}`} label={`${label(language)} audio`} ok={ready} text={ready ? 'Ready' : (audioStatus[language] || 'Background')} />)}<Readiness label="Publish" ok={readiness.publish} text={readiness.publish ? 'Unlocked' : 'Needs text approval'} /></div><div className="side-hint"><strong>Why is it blocked?</strong><p>{readiness.publish ? 'The lesson can publish now. Optional language versions, audio, and practice can be generated later.' : readiness.problems.join(' • ')}</p></div></Card><Card><SectionTitle title="Core loop" /><div className="mini-flow"><span>Speak / write</span><b>↓</b><span>Concepts</span><b>↓</b><span>Translate + review</span><b>↓</b><span>Optional audio + practice</span><b>↓</b><span>Publish + learn</span></div></Card></aside>
      </div>
    </Page>

    {worksheetOpen && <Modal title="Worksheet preview" onClose={() => setWorksheetOpen(false)} wide><div className="worksheet-preview-actions"><Status tone="success">No pop-up required</Status><Button variant="secondary" onClick={() => worksheetFrame.current?.contentWindow?.print()}><Printer size={15} /> Print / Save PDF</Button><Button variant="ghost" onClick={() => setWorksheetOpen(false)}>Close</Button></div><iframe ref={worksheetFrame} className="worksheet-frame" title="BhashaSaathi worksheet" srcDoc={worksheetHtml} /></Modal>}
  </>;
}

function Readiness({ label, ok, text }: { label: string; ok: boolean; text: string }) {
  return <div><span>{label}</span><Status tone={ok ? 'success' : 'warning'}>{text}</Status></div>;
}
