import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, BookOpen, Download, Languages, RefreshCw, Wifi, WifiOff } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Page, SectionTitle } from '../components/Shell';
import { Button, Card, Empty, Field, Modal, Status, useToast } from '../components/UI';
import { api, errorMessage } from '../lib/api';
import { all, get, put } from '../lib/idb';
import type { Lang, LessonPack } from '../types';
import { useAuth } from '../features/auth/AuthContext';

const labels: Record<Lang, string> = { eng_Latn: 'English', hin_Deva: 'हिन्दी', mar_Deva: 'मराठी', sat_Olck: 'ᱥᱟᱱᱛᱟᱲᱤ' };

export default function StudentHomePage() {
  const { user } = useAuth();
  const { push } = useToast();
  const [lessons, setLessons] = useState<LessonPack[]>([]);
  const [progress, setProgress] = useState<any>(null);
  const [language, setLanguage] = useState<Lang>((localStorage.getItem('student_language') as Lang) || 'eng_Latn');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [joinOpen, setJoinOpen] = useState(false);
  const [code, setCode] = useState('');
  const [downloading, setDownloading] = useState<number | null>(null);

  const load = async () => {
    try {
      setLoading(true);
      const [packs, p] = await Promise.all([api.studentLessons(), api.progress()]);
      setLessons(packs); setProgress(p); setError('');
    } catch (err) {
      try { const cached = await all<LessonPack>('lessons'); if (cached.length) setLessons(cached); else setError(errorMessage(err)); }
      catch { setError(errorMessage(err)); }
    } finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);

  const availableCount = useMemo(() => lessons.length, [lessons]);
  const setLang = (lang: Lang) => { setLanguage(lang); localStorage.setItem('student_language', lang); };

  const download = async (pack: LessonPack) => {
    setDownloading(pack.lesson.id);
    try {
      await put('lessons', String(pack.lesson.id), pack);
      for (const artifact of pack.artifacts.filter((item) => item.type === 'audio')) {
        try { await put('audio', String(artifact.id), await api.fetchArtifact(artifact.id)); } catch { /* keep content even if one audio artifact fails */ }
      }
      await put('app_metadata', `cached:${pack.lesson.id}`, { cachedAt: Date.now(), version: pack.version.version_number });
      push(`${pack.lesson.title} is available offline.`);
    } catch (err) { push(errorMessage(err), 'error'); }
    finally { setDownloading(null); }
  };

  const join = async () => {
    try {
      const result = await api.joinGroup(code);
      setJoinOpen(false); setCode(''); push(result.already_member ? `You're already in ${result.group.name}.` : `Joined ${result.group.name}.`); await load();
    } catch (err) { push(errorMessage(err), 'error'); }
  };

  if (loading) return <Page title="Opening your learning space…"><Card><div className="loading-inline"><RefreshCw size={18} className="spin" /> Loading published lessons…</div></Card></Page>;

  return <Page eyebrow="MY LEARNING" title="Learn in the language that feels like home." subtitle="Download published lessons once, then keep reading, listening and practicing when connectivity is gone." actions={<div className="head-action-row"><Button variant="secondary" onClick={() => setJoinOpen(true)}>Join classroom</Button><Button variant="ghost" onClick={() => void load()}><RefreshCw size={15} /> Refresh</Button></div>}>
    {error && <Card className="error-card"><Status tone="danger">{error}</Status><button onClick={() => void load()}>Try again</button></Card>}
    <div className="student-welcome"><div><span className="eyebrow">WELCOME, {user?.name?.split(' ')[0]?.toUpperCase()}</span><h2>Your next lesson is already close.</h2><p>Select your learning language, open a published lesson and download it before heading into a low-connectivity area.</p></div><div className="welcome-state"><Wifi size={19} /><strong>Online now</strong><span>Sync whenever you need it.</span></div></div>
    <div className="student-overview"><Card><SectionTitle title="Learning language" subtitle="Switch any time." /><div className="language-choice">{(['eng_Latn', 'hin_Deva', 'mar_Deva', 'sat_Olck'] as Lang[]).map((lang) => <button key={lang} className={language === lang ? 'selected' : ''} onClick={() => setLang(lang)}><strong>{labels[lang]}</strong><span>{lang === 'mar_Deva' ? 'Marathi · Devanagari' : lang === 'hin_Deva' ? 'Hindi · Devanagari' : lang === 'sat_Olck' ? 'Santali · Ol Chiki' : 'English · Latin'}</span></button>)}</div></Card><Card><SectionTitle title="Your progress" subtitle="Simple, concept-first feedback." /><div className="progress-hero"><strong>{Math.round(progress?.overall_understanding || 0)}%</strong><div><span>Overall understanding</span><small>{progress?.lessons_completed || 0} lesson attempt{progress?.lessons_completed === 1 ? '' : 's'}</small></div></div><div className="progress-line"><i style={{ width: `${Math.min(100, Math.max(0, progress?.overall_understanding || 0))}%` }} /></div></Card></div>
    <Card><SectionTitle title="Published lessons" subtitle={`${availableCount} available lesson${availableCount === 1 ? '' : 's'} in your joined classrooms.`} />{lessons.length === 0 ? <Empty title="No published lessons yet" body="Join another classroom or wait for your educator to publish a lesson." action={<Button onClick={() => setJoinOpen(true)}>Join classroom</Button>} /> : <div className="student-course-grid">{lessons.map((pack) => <article className="course-card" key={pack.lesson.id}><div className="course-top"><div className="course-icon"><BookOpen size={20} /></div><Status tone="success">Published</Status></div><span className="eyebrow">{pack.lesson.grade} · {pack.lesson.subject}</span><h3>{pack.lesson.title}</h3><p>{pack.lesson.topic || 'Explore this lesson in your chosen language.'}</p><div className="course-footer"><Link to={`/student/lessons/${pack.lesson.id}`}><Button>Open lesson <ArrowRight size={15} /></Button></Link><Button variant="secondary" onClick={() => void download(pack)} disabled={downloading === pack.lesson.id}><Download size={15} />{downloading === pack.lesson.id ? 'Saving…' : 'Offline'}</Button></div></article>)}</div>}</Card>
    {joinOpen && <Modal title="Join a classroom" onClose={() => setJoinOpen(false)}><div className="join-code-hero"><Languages size={20} /><div><strong>Enter your educator's classroom code</strong><span>Joining is safe — your account only gets learner access.</span></div></div><Field label="Join code"><input autoFocus className="code-input" value={code} onChange={(e) => setCode(e.target.value.toUpperCase().replace(/\s/g, ''))} placeholder="A1B2C3D" /></Field><div className="modal-actions"><Button variant="ghost" onClick={() => setJoinOpen(false)}>Cancel</Button><Button onClick={join} disabled={code.length < 4}>Join classroom</Button></div></Modal>}
  </Page>;
}
