import { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, CheckCircle2, Download, Headphones, Play, RefreshCw, Volume2, Wifi, WifiOff } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';
import { Page, SectionTitle } from '../components/Shell';
import { Button, Card, Status, useToast } from '../components/UI';
import { api, errorMessage } from '../lib/api';
import { get, put } from '../lib/idb';
import { queueAttempt, syncNow } from '../lib/sync';
import type { Lang, LessonPack } from '../types';
import { useAuth } from '../features/auth/AuthContext';

const languageName = (lang: Lang) => ({ eng_Latn: 'English', hin_Deva: 'हिन्दी', mar_Deva: 'मराठी', sat_Olck: 'ᱥᱟᱱᱛᱟᱲᱤ' } as Record<Lang, string>)[lang];

export default function StudentLessonPage() {
  const { id } = useParams();
  const lessonId = Number(id);
  const { user } = useAuth();
  const { push } = useToast();
  const [pack, setPack] = useState<LessonPack | null>(null);
  const [language, setLanguage] = useState<Lang>((localStorage.getItem('student_language') as Lang) || 'eng_Latn');
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState('');
  const [offline, setOffline] = useState(!navigator.onLine);
  const [audioUrls, setAudioUrls] = useState<Record<number, string>>({});
  const [cached, setCached] = useState(false);
  const [audioLoading, setAudioLoading] = useState<number | null>(null);
  const [feedbackBusy, setFeedbackBusy] = useState<number | null>(null);

  useEffect(() => {
    if (!pack) return;
    const preferred = localStorage.getItem('student_language') as Lang | null;
    const candidates = [preferred, pack.lesson.source_language, ...pack.translations.filter((item) => item.verification_status === 'TEACHER_APPROVED' || (item.language === 'sat_Olck' && item.verification_status === 'TEMPORARY')).map((item) => item.language)].filter(Boolean) as Lang[];
    const usable = candidates.find((lang) => lang === pack.lesson.source_language || pack.translations.some((item) => item.language === lang));
    if (usable) setLanguage(usable);
  }, [pack?.lesson.source_language, pack?.translations.length]);

  const load = async () => {
    try {
      const onlinePack = await api.studentPackage(lessonId);
      setPack(onlinePack);
      setCached(false);
      setError('');
      await put('lessons', String(lessonId), onlinePack);
    } catch (err) {
      const localPack = await get<LessonPack>('lessons', String(lessonId));
      if (localPack) {
        setPack(localPack);
        setCached(true);
        setError('');
      } else {
        setError(errorMessage(err));
      }
    }
  };

  useEffect(() => {
    void load();
    const on = () => { setOffline(false); void syncNow(); void load(); };
    const off = () => setOffline(true);
    window.addEventListener('online', on);
    window.addEventListener('offline', off);
    return () => { window.removeEventListener('online', on); window.removeEventListener('offline', off); };
  }, [lessonId]);

  const translation = pack?.translations.find((item) => item.language === language);
  const text = language === pack?.lesson.source_language ? pack?.version.clean_transcript || pack?.version.source_text : translation?.text;
  const questions = useMemo(() => pack?.practice.filter((question) => question.language === language) || [], [pack, language]);
  const audioArtifacts = pack?.artifacts.filter((item) => item.type === 'audio' && item.language === language) || [];

  const loadAudio = async (artifactId: number) => {
    if (audioUrls[artifactId]) return;
    setAudioLoading(artifactId);
    try {
      const local = await get<Blob>('audio', String(artifactId));
      const blob = local || await api.fetchArtifact(artifactId);
      setAudioUrls((old) => ({ ...old, [artifactId]: URL.createObjectURL(blob) }));
      if (!local) await put('audio', String(artifactId), blob);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setAudioLoading(null);
    }
  };

  const sendTranslationFeedback = async (translationId: number, status: 'CONFIRMED' | 'REPORTED') => {
    setFeedbackBusy(translationId);
    try {
      await api.translationFeedback(translationId, status);
      setPack((old) => old ? {
        ...old,
        translations: old.translations.map((item) => item.id === translationId ? {
          ...item,
          my_student_feedback: status,
          student_feedback: {
            confirmed: (item.student_feedback?.confirmed || 0) + (item.my_student_feedback === 'CONFIRMED' && status !== 'CONFIRMED' ? -1 : 0) + (item.my_student_feedback !== 'CONFIRMED' && status === 'CONFIRMED' ? 1 : 0),
            reported: (item.student_feedback?.reported || 0) + (item.my_student_feedback === 'REPORTED' && status !== 'REPORTED' ? -1 : 0) + (item.my_student_feedback !== 'REPORTED' && status === 'REPORTED' ? 1 : 0),
          },
        } : item),
      } : old);
      push(status === 'CONFIRMED' ? 'Thanks — this translation was marked as correct.' : 'Thanks — this translation was flagged for teacher review.');
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setFeedbackBusy(null);
    }
  };

  const download = async () => {
    if (!pack) return;
    try {
      await put('lessons', String(pack.lesson.id), pack);
      for (const artifact of pack.artifacts.filter((item) => item.type === 'audio')) {
        if (await get<Blob>('audio', String(artifact.id))) continue;
        try { await put('audio', String(artifact.id), await api.fetchArtifact(artifact.id)); } catch { /* audio can be loaded later */ }
      }
      await put('app_metadata', `cached:${pack.lesson.id}`, { cachedAt: Date.now(), version: pack.version.version_number });
      setCached(true);
      push('This lesson is ready for offline use.');
    } catch (err) {
      push(errorMessage(err), 'error');
    }
  };

  const submit = async () => {
    if (!pack) return;
    if (!questions.length) {
      setError(`Practice is not prepared in ${languageName(language)}.`);
      return;
    }
    const payload = {
      lessonId,
      lessonVersionId: pack.version.id,
      language,
      answers,
      createdAt: new Date().toISOString(),
    };
    if (navigator.onLine) {
      try {
        const response = await api.attempt({ lesson_id: lessonId, lesson_version_id: pack.version.id, language, answers });
        setResult(response);
        push('Practice submitted.');
      } catch (err) {
        setError(errorMessage(err));
      }
    } else {
      await queueAttempt({ localId: crypto.randomUUID(), ...payload });
      setResult({ score: null, pending: true });
      push('Answer saved offline. It will sync when connection returns.');
    }
  };

  if (!pack) {
    return <Page title="Opening lesson…"><Card>{error ? <div className="stack-sm"><Status tone="danger">{error}</Status><Button variant="secondary" onClick={() => { setError(''); void load(); }}><RefreshCw size={15} /> Retry</Button></div> : <div className="loading-inline"><RefreshCw size={18} className="spin" /> Loading lesson pack…</div>}</Card></Page>;
  }

  return <Page eyebrow={`${pack.lesson.grade} · ${pack.lesson.subject}`} title={pack.lesson.title} subtitle={pack.lesson.topic || 'Explore the lesson, then practice the concepts.'} actions={<div className="head-action-row"><Link to="/student"><Button variant="ghost"><ArrowLeft size={16} /> My learning</Button></Link><Button variant="secondary" onClick={() => void download()}><Download size={15} /> {cached ? 'Offline ready' : 'Download offline'}</Button></div>}>
    <div className="lesson-status-bar"><div>{offline || cached ? <Status tone="warning"><WifiOff size={15} /> {offline ? 'Offline mode' : 'Cached on this device'}</Status> : <Status tone="success"><Wifi size={15} /> Online</Status>}</div><div className="language-switcher">{[pack.lesson.source_language, ...pack.translations.filter((item) => item.verification_status === 'TEACHER_APPROVED' || (item.language === 'sat_Olck' && item.verification_status === 'TEMPORARY')).map((item) => item.language)].filter((lang, index, items): lang is Lang => items.indexOf(lang) === index).map((lang) => <button key={lang} className={language === lang ? 'selected' : ''} onClick={() => { setLanguage(lang); localStorage.setItem('student_language', lang); setAnswers({}); setResult(null); }}>{languageName(lang)}</button>)}</div></div>
    {error && <Card className="error-card"><Status tone="danger">{error}</Status><button onClick={() => setError('')}>Dismiss</button></Card>}
    <div className="lesson-main-grid"><Card className="lesson-reader"><div className="reader-kicker"><span>LESSON CONTENT</span>{translation ? <Status tone={translation.verification_status === 'TEACHER_APPROVED' ? 'success' : 'warning'}>{translation.verification_status.replaceAll('_', ' ')}</Status> : <Status>Source language</Status>}</div><div className={`lesson-script ${language === 'mar_Deva' || language === 'hin_Deva' ? 'devanagari' : ''} ${language === 'sat_Olck' ? 'olchiki' : ''}`}>{text || 'This language version has not been prepared for students yet.'}</div>{translation?.flags?.length ? <div className="info-banner warning"><span>Translation Guard</span><p>This version includes review diagnostics. Review the version status before using it for formal instruction.</p></div> : null}
      {translation?.verification_status === 'TEMPORARY' && <div className="info-banner warning"><span>Temporary Santali version</span><p>This language is available for demo/classroom exploration without the normal approval gate. A teacher can replace or review it later.</p></div>}{translation && <div className="info-banner"><CheckCircle2 size={16} /><div><strong>Language check</strong><p>Students can confirm the translation or report an issue. This feedback helps the educator review target-language content later.</p><div className="button-row wrap"><Button variant={translation.my_student_feedback === 'CONFIRMED' ? 'primary' : 'secondary'} onClick={() => void sendTranslationFeedback(translation.id, 'CONFIRMED')} disabled={feedbackBusy === translation.id}>✓ Looks correct</Button><Button variant={translation.my_student_feedback === 'REPORTED' ? 'danger' : 'secondary'} onClick={() => void sendTranslationFeedback(translation.id, 'REPORTED')} disabled={feedbackBusy === translation.id}>Report an issue</Button></div><small>{translation.student_feedback?.confirmed || 0} student confirmation(s) · {translation.student_feedback?.reported || 0} report(s)</small></div></div>}
      <div className="audio-block"><SectionTitle title="Listen" subtitle="Audio is downloaded with the published lesson package." />{audioArtifacts.length ? <div className="audio-list">{audioArtifacts.map((artifact) => <div className="student-audio" key={artifact.id}><div><Volume2 size={17} /><div><strong>{languageName(language)} audio</strong><span>Cached classroom audio</span></div></div>{audioUrls[artifact.id] ? <audio controls autoPlay={false} src={audioUrls[artifact.id]} /> : <Button variant="secondary" onClick={() => void loadAudio(artifact.id)} disabled={audioLoading === artifact.id}>{audioLoading === artifact.id ? 'Loading…' : <><Play size={15} /> Load audio</>}</Button>}</div>)}</div> : <Status tone="warning">Audio has not been prepared for this language yet.</Status>}</div></Card>
      <Card className="lesson-side-card"><SectionTitle title="Learning at a glance" /><div className="concept-chip-list">{pack.version.concepts.map((concept) => <span key={concept.concept_id}>{concept.label}</span>)}</div><div className="side-rule" /><div className="side-info"><Headphones size={17} /><div><strong>Listen</strong><span>Play cached audio whenever it is available.</span></div></div><div className="side-info"><Download size={17} /><div><strong>Offline</strong><span>Download once, then continue without internet.</span></div></div></Card></div>
    <Card><SectionTitle title="Practice your understanding" subtitle="Answer the educator-approved questions. Your score is based on the concept checks prepared for this lesson." />{questions.length === 0 ? <Status tone="warning">Practice has not been approved in {languageName(language)} yet.</Status> : <><div className="practice-list">{questions.map((question, index) => <div className="practice-card" key={question.id}><div className="practice-number">{String(index + 1).padStart(2, '0')}</div><div className="practice-body"><strong>{question.prompt}</strong><div className="option-list">{question.options.map((option) => <label key={option} className={answers[String(question.id)] === option ? 'option selected' : 'option'}><input type="radio" name={`question-${question.id}`} checked={answers[String(question.id)] === option} onChange={() => setAnswers((old) => ({ ...old, [String(question.id)]: option }))} /><span>{option}</span></label>)}</div></div></div>)}</div><div className="practice-submit"><Button onClick={() => void submit()} disabled={questions.some((question) => !answers[String(question.id)])}><CheckCircle2 size={16} /> {offline ? 'Save offline answer' : 'Submit answers'}</Button>{result?.pending && <Status tone="warning">Saved locally · waiting for sync</Status>}{result && result.score !== null && <div className="score-box"><strong>{result.score}%</strong><span>Concept understanding</span></div>}</div></>}</Card>
  </Page>;
}
