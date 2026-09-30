import { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, ArrowRight, BookOpen, Copy, Plus, RefreshCw, UserPlus, Users } from 'lucide-react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Page, SectionTitle } from '../components/Shell';
import { Button, Card, Empty, Field, Modal, Status, useToast } from '../components/UI';
import { api, errorMessage } from '../lib/api';
import type { Group, LessonSummary } from '../types';
import { useAuth } from '../features/auth/AuthContext';

export default function GroupPage() {
  const { id } = useParams();
  const groupId = Number(id);
  const navigate = useNavigate();
  const { user } = useAuth();
  const [group, setGroup] = useState<Group | null>(null);
  const isOwner = group?.owner_id === user?.id;
  const [lessons, setLessons] = useState<LessonSummary[]>([]);
  const [members, setMembers] = useState<Array<{ id: number; user_id: number; name: string; email: string; role: string; joined_at: string }>>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [lessonForm, setLessonForm] = useState<{ title: string; topic: string; source: string; source_language: import('../types').Lang }>({ title: '', topic: '', source: '', source_language: 'eng_Latn' });
  const [busy, setBusy] = useState(false);
  const { push } = useToast();

  const load = async (background = false) => {
    try {
      if (background) setRefreshing(true); else setLoading(true);
      const [g, l] = await Promise.all([api.group(groupId), api.lessons(groupId)]);
      setGroup(g); setLessons(l);
      if (isOwner) {
        try { setMembers(await api.members(groupId)); } catch { setMembers([]); }
      }
      setError('');
    } catch (err) { setError(errorMessage(err)); }
    finally { setLoading(false); setRefreshing(false); }
  };
  useEffect(() => { if (Number.isFinite(groupId)) void load(); }, [groupId, isOwner]);

  const visibleLessons = useMemo(() => isOwner ? lessons : lessons.filter((lesson) => lesson.status === 'PUBLISHED'), [lessons, isOwner]);

  const createLesson = async () => {
    if (!lessonForm.title.trim()) return;
    setBusy(true); setError('');
    try {
      const lesson = await api.createLesson(groupId, { title: lessonForm.title.trim(), source_text: lessonForm.source, source_language: lessonForm.source_language, grade: group?.grade || 'Grade 2', subject: group?.subject || 'Environmental Studies', topic: lessonForm.topic });
      setCreateOpen(false); setLessonForm({ title: '', topic: '', source: '', source_language: 'eng_Latn' });
      push('Lesson draft created.');
      navigate(`/lessons/${lesson.id}/builder`);
    } catch (err) { const message = errorMessage(err); setError(message); push(message, 'error'); }
    finally { setBusy(false); }
  };

  const copyCode = async () => { if (!group) return; try { await navigator.clipboard.writeText(group.join_code); push('Join code copied.'); } catch { push('Could not copy the code.', 'error'); } };

  if (loading) return <Page title="Opening classroom…"><Card><div className="loading-inline"><RefreshCw className="spin" size={18} /> Loading classroom data…</div></Card></Page>;
  if (!group) return <Page title="Classroom unavailable"><Card className="error-card"><Status tone="danger">{error || 'This classroom could not be opened.'}</Status><Link to="/groups"><Button>Back to classrooms</Button></Link></Card></Page>;

  return <Page eyebrow={`${group.grade.toUpperCase()} · ${group.subject.toUpperCase()}`} title={group.name} subtitle={isOwner ? 'Manage lessons, learner access and the classroom join code.' : 'Published lessons available to you in this classroom.'} actions={<div className="head-action-row"><Button variant="ghost" onClick={() => navigate('/groups')}><ArrowLeft size={16} /> Classrooms</Button>{isOwner && <Button onClick={() => setCreateOpen(true)}><Plus size={16} /> New lesson</Button>}</div>}>
    {error && <Card className="error-card"><Status tone="danger">{error}</Status><button onClick={() => void load(true)}>Retry</button></Card>}
    <div className="group-hero"><div className="group-hero-main"><div className="group-avatar large"><Users size={28} /></div><div><span className="eyebrow">CLASSROOM</span><h2>{group.name}</h2><p>{group.grade} · {group.subject}</p></div></div><div className="join-box"><span>Share this code</span><strong>{group.join_code}</strong><Button variant="ghost" onClick={copyCode}><Copy size={15} /> Copy code</Button></div></div>
    <div className="metric-grid metric-grid-three"><Card><div className="metric-icon"><Users /></div><strong>{Math.max(0, group.members - (isOwner ? 1 : 0))}</strong><span>Learners</span><small>{isOwner ? 'Student members' : 'People in this classroom'}</small></Card><Card><div className="metric-icon"><BookOpen /></div><strong>{visibleLessons.length}</strong><span>{isOwner ? 'Lessons' : 'Published lessons'}</span><small>{isOwner ? 'Drafts + published' : 'Ready to learn'}</small></Card><Card><div className="metric-icon"><UserPlus /></div><strong>{isOwner ? group.join_code : 'Joined'}</strong><span>{isOwner ? 'Join code' : 'Membership'}</span><small>{isOwner ? 'Share with learners' : 'You are a member'}</small></Card></div>
    <div className="content-grid two-thirds"><Card><SectionTitle title="Lessons" subtitle={isOwner ? 'Create a draft, prepare AI outputs and publish only after review.' : 'Only published lessons are shown to learners.'} action={<button className="icon-text-btn" onClick={() => void load(true)} disabled={refreshing}><RefreshCw size={15} className={refreshing ? 'spin' : ''} /> Refresh</button>} />{visibleLessons.length === 0 ? <Empty title={isOwner ? 'No lessons yet' : 'Nothing published yet'} body={isOwner ? 'Create a lesson in any supported source language to start the BhashaSaathi pipeline.' : 'Your educator has not published a lesson in this classroom yet.'} action={isOwner ? <Button onClick={() => setCreateOpen(true)}><Plus size={16} /> Create lesson</Button> : undefined} /> : <div className="stack-list">{visibleLessons.map((lesson) => <Link key={lesson.id} className="row-card" to={isOwner ? `/lessons/${lesson.id}/builder` : `/student/lessons/${lesson.id}`}><div className="row-icon"><BookOpen size={18} /></div><div className="row-main"><strong>{lesson.title}</strong><span>{lesson.topic || lesson.subject}</span><small>Updated {lesson.updated_at ? new Date(lesson.updated_at).toLocaleString() : '—'}</small></div><div className="row-meta"><span className={`status-dot ${lesson.status === 'PUBLISHED' ? 'published' : lesson.status === 'FLAGGED' ? 'flagged' : ''}`}>{lesson.status.replaceAll('_', ' ')}</span><span>v{lesson.version_number ?? 1}</span><ArrowRight size={16} /></div></Link>)}</div>}</Card><div className="stack"><Card><SectionTitle title="Classroom health" subtitle="A quick snapshot before the lesson demo." /><div className="readiness-list"><div><span>Join code</span><Status tone="success">Active</Status></div><div><span>Published lessons</span><Status tone={lessons.some((l) => l.status === 'PUBLISHED') ? 'success' : 'warning'}>{lessons.some((l) => l.status === 'PUBLISHED') ? 'Available' : 'None yet'}</Status></div><div><span>Offline delivery</span><Status tone="info">Available after publish</Status></div></div></Card>{isOwner && <Card><SectionTitle title="Learners" subtitle={`${members.length || Math.max(1, group.members)} member record(s).`} />{members.length ? <div className="member-list">{members.map((member) => <div key={member.id} className="member-row"><div className="avatar sm">{member.name.slice(0, 1).toUpperCase()}</div><div><strong>{member.name}</strong><span>{member.email}</span></div><b>{member.role}</b></div>)}</div> : <p className="muted">Member details are unavailable right now. The classroom count is still safe to use.</p>}</Card>}</div></div>
    {createOpen && <Modal title="Create lesson" onClose={() => setCreateOpen(false)} wide><div className="modal-intro"><div className="modal-icon"><BookOpen size={18} /></div><div><strong>Start with the teacher's original lesson.</strong><p>Keep the source simple. You can add concepts, translations, audio and practice inside the builder.</p></div></div><div className="form-stack"><Field label="Lesson title"><input autoFocus value={lessonForm.title} onChange={(e) => setLessonForm({ ...lessonForm, title: e.target.value })} placeholder="Why Plants Need Water" /></Field><Field label="Source language"><select value={lessonForm.source_language} onChange={(e) => setLessonForm({ ...lessonForm, source_language: e.target.value as import('../types').Lang })}><option value="eng_Latn">English · मुख्य</option><option value="hin_Deva">हिन्दी · Hindi</option><option value="mar_Deva">मराठी · Marathi</option><option value="sat_Olck">ᱥᱟᱱᱛᱟᱲᱤ · Santali</option></select></Field><Field label="Topic" hint="Optional"><input value={lessonForm.topic} onChange={(e) => setLessonForm({ ...lessonForm, topic: e.target.value })} placeholder="Plants and Water" /></Field><Field label="Original lesson" hint="Choose the source language below. You can also record audio in the next step."><textarea rows={8} value={lessonForm.source} onChange={(e) => setLessonForm({ ...lessonForm, source: e.target.value })} placeholder="Today we will learn why plants need water…" /></Field></div><div className="modal-actions"><Button variant="ghost" onClick={() => setCreateOpen(false)}>Cancel</Button><Button onClick={createLesson} disabled={busy || lessonForm.title.trim().length < 2}>{busy ? 'Creating draft…' : 'Create draft & open builder'}</Button></div></Modal>}
  </Page>;
}
