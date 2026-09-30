import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, BookOpen, CircleCheck, Cpu, Plus, ShieldCheck, Users } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Page, SectionTitle } from '../components/Shell';
import { Button, Card, Empty, Status, useToast } from '../components/UI';
import { api, errorMessage } from '../lib/api';
import type { Group } from '../types';

export default function AdminDashboardPage() {
  const [groups, setGroups] = useState<Group[]>([]);
  const [models, setModels] = useState<any>();
  const [error, setError] = useState('');
  const { push } = useToast();

  const load = async () => {
    try {
      const [g, m] = await Promise.all([api.groups(), api.modelHealth()]);
      setGroups(g); setModels(m); setError('');
    } catch (err) { setError(errorMessage(err)); }
  };
  useEffect(() => { void load(); }, []);

  const lessons = useMemo(() => groups.reduce((total, group) => total + group.lessons, 0), [groups]);
  const learners = useMemo(() => groups.reduce((total, group) => total + Math.max(0, group.members - 1), 0), [groups]);
  const translationReady = models ? Object.values(models.translation || {}).every(Boolean) : false;

  return <Page eyebrow="EDUCATOR WORKSPACE" title="Build once. Teach in every language." subtitle="Your classroom command center for creating, reviewing and publishing multilingual lessons." actions={<Link to="/groups?create=1"><Button><Plus size={17} /> New classroom</Button></Link>}>
    {error && <Card className="error-card"><Status tone="danger">{error}</Status><button onClick={() => { setError(''); void load(); }}>Try again</button></Card>}
    <div className="hero-band"><div><div className="hero-kicker">THE BHASHASAATHI LOOP</div><h2>Teacher voice → concepts → verified language → offline learning.</h2><p>The educator stays in control at every publish point.</p></div><div className="hero-badge"><CircleCheck size={16} /><span>Local-first workflow</span></div></div>
    <div className="metric-grid metric-grid-four"><Card><div className="metric-icon"><Users /></div><strong>{groups.length}</strong><span>Classrooms</span><small>Teacher-owned spaces</small></Card><Card><div className="metric-icon"><BookOpen /></div><strong>{lessons}</strong><span>Lessons</span><small>Drafts + published versions</small></Card><Card><div className="metric-icon"><ShieldCheck /></div><strong>{learners}</strong><span>Learners</span><small>Across your classrooms</small></Card><Card><div className="metric-icon"><Cpu /></div><strong>{translationReady ? 'Ready' : models ? 'Check' : '…'}</strong><span>Translation runtime</span><small>IndicTrans2 local model</small></Card></div>
    <div className="content-grid two-thirds"><Card><SectionTitle title="Your classrooms" subtitle="Open a classroom to create lessons and manage learners." action={<Link className="small-link" to="/groups">View all <ArrowRight size={15} /></Link>} />{groups.length === 0 ? <Empty title="Start your first classroom" body="Create a group, share the join code, and then build your first lesson." action={<Link to="/groups?create=1"><Button>Create classroom</Button></Link>} /> : <div className="stack-list">{groups.slice(0, 6).map((group) => <Link key={group.id} to={`/groups/${group.id}`} className="row-card"><div className="row-icon"><Users size={18} /></div><div className="row-main"><strong>{group.name}</strong><span>{group.grade} · {group.subject}</span></div><div className="row-meta"><span>{Math.max(0, group.members - 1)} learners</span><span>{group.lessons} lessons</span><ArrowRight size={16} /></div></Link>)}</div>}</Card><Card><SectionTitle title="Before a demo" subtitle="Quick readiness checks." /><div className="readiness-list"><div><span>Translation models</span><Status tone={translationReady ? 'success' : 'danger'}>{translationReady ? 'Ready' : 'Check folders'}</Status></div><div><span>Whisper Small</span><Status tone={models?.stt ? 'success' : 'danger'}>{models?.stt ? 'Ready' : 'Missing'}</Status></div><div><span>Parler-TTS</span><Status tone={models?.tts?.runtime_ready ? 'success' : 'warning'}>{models?.tts?.runtime_ready ? 'Ready' : models?.tts?.model ? 'Tokenizer support needed' : 'Model missing'}</Status></div></div><div className="tip-box"><strong>Demo flow</strong><p>Create a Hindi lesson → analyze → translate to Marathi → review → publish → open as student → download offline.</p><Link className="small-link" to="/diagnostics">Open diagnostics <ArrowRight size={14} /></Link></div></Card></div>
    <div className="footer-note">BhashaSaathi keeps original teacher content separate from AI suggestions and never treats unreviewed Marathi as automatically verified.</div>
  </Page>;
}
