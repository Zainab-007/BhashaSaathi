import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Copy, Plus, Search, Users, ArrowRight, LogIn, X } from 'lucide-react';
import { Page, SectionTitle } from '../components/Shell';
import { Button, Card, Empty, Field, Modal, Status, useToast } from '../components/UI';
import { api, errorMessage } from '../lib/api';
import type { Group } from '../types';
import { useAuth } from '../features/auth/AuthContext';

export default function GroupsPage() {
  const { user } = useAuth();
  const canCreate = true;
  const [params, setParams] = useSearchParams();
  const [groups, setGroups] = useState<Group[]>([]);
  const [filter, setFilter] = useState('');
  const [showCreate, setShowCreate] = useState(params.get('create') === '1');
  const [showJoin, setShowJoin] = useState(false);
  const [form, setForm] = useState({ name: '', grade: 'Grade 2', subject: 'Environmental Studies' });
  const [joinCode, setJoinCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const { push } = useToast();

  const load = async () => {
    try { setGroups(await api.groups()); setError(''); }
    catch (err) { setError(errorMessage(err)); }
  };
  useEffect(() => { void load(); }, []);
  useEffect(() => { setParams((current) => { current.delete('create'); return current; }, { replace: true }); }, []);

  const filtered = useMemo(() => groups.filter((g) => `${g.name} ${g.grade} ${g.subject}`.toLowerCase().includes(filter.toLowerCase())), [groups, filter]);

  const close = (mode: 'create' | 'join') => { if (mode === 'create') setShowCreate(false); else setShowJoin(false); };

  const create = async () => {
    if (form.name.trim().length < 2) return;
    setBusy(true); setError('');
    try {
      const group = await api.createGroup({ name: form.name.trim(), grade: form.grade.trim() || 'Grade 2', subject: form.subject.trim() || 'Environmental Studies' });
      await load();
      setShowCreate(false); setForm({ name: '', grade: 'Grade 2', subject: 'Environmental Studies' });
      push(`Classroom created. Join code: ${group.join_code}`);
    } catch (err) { setError(errorMessage(err)); push(errorMessage(err), 'error'); }
    finally { setBusy(false); }
  };

  const join = async () => {
    if (joinCode.trim().length < 4) return;
    setBusy(true); setError('');
    try {
      const result = await api.joinGroup(joinCode);
      await load(); setShowJoin(false); setJoinCode('');
      push(result.already_member ? `You're already in ${result.group.name}.` : `Joined ${result.group.name}.`);
    } catch (err) { setError(errorMessage(err)); push(errorMessage(err), 'error'); }
    finally { setBusy(false); }
  };

  return <Page eyebrow="CLASSROOMS" title="Your classrooms" subtitle="Create a classroom to teach, or join one to learn. The same account can do both." actions={<div className="head-action-row"><Button variant="secondary" onClick={() => setShowJoin(true)}><LogIn size={16} /> Join classroom</Button><Button onClick={() => setShowCreate(true)}><Plus size={16} /> Create classroom</Button></div>}>
    {error && <Card className="error-card"><Status tone="danger">{error}</Status><button onClick={() => void load()}>Reload classrooms</button></Card>}
    <Card className="toolbar-card"><div className="search-box"><Search size={17} /><input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Search by classroom, grade or subject…" /></div><span className="toolbar-count">{filtered.length} classroom{filtered.length === 1 ? '' : 's'}</span></Card>
    {filtered.length === 0 ? <Empty title={filter ? 'No matching classrooms' : 'No classrooms yet'} body={filter ? 'Try another search.' : 'Create a classroom to teach, or use a join code to learn.'} action={!filter ? <div className="button-row"><Button onClick={() => setShowCreate(true)}><Plus size={16} /> Create classroom</Button><Button variant="secondary" onClick={() => setShowJoin(true)}><LogIn size={16} /> Join classroom</Button></div> : undefined} /> : <div className="classroom-grid">{filtered.map((group) => <Link to={`/groups/${group.id}`} key={group.id} className="classroom-card"><div className="classroom-card-top"><div className="group-avatar"><Users size={21} /></div><span className="status-pill">{group.grade}</span></div><strong>{group.name}</strong><span>{group.subject}</span><div className="classroom-card-footer"><span>{canCreate ? Math.max(0, group.members - 1) : group.members} learner{Math.max(0, group.members - 1) === 1 ? '' : 's'}</span><span>{group.lessons} lesson{group.lessons === 1 ? '' : 's'}</span><ArrowRight size={16} /></div></Link>)}</div>}

    {showCreate && <Modal title="Create a new classroom" onClose={() => close('create')}>
      <div className="modal-intro"><div className="modal-icon"><Plus size={18} /></div><div><strong>Set up the learning space first.</strong><p>You can change lesson content later. The generated join code will be shown after creation.</p></div></div>
      <div className="form-stack"><Field label="Classroom name" hint="Example: Demo Primary Classroom"><input autoFocus value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Demo Primary Classroom" /></Field><Field label="Grade"><input value={form.grade} onChange={(e) => setForm({ ...form, grade: e.target.value })} placeholder="Grade 2" /></Field><Field label="Subject"><input value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} placeholder="Environmental Studies" /></Field></div>
      <div className="modal-actions"><Button variant="ghost" onClick={() => close('create')}>Cancel</Button><Button onClick={create} disabled={busy || form.name.trim().length < 2}>{busy ? 'Creating…' : 'Create classroom'}</Button></div>
    </Modal>}

    {showJoin && <Modal title="Join a classroom" onClose={() => close('join')}>
      <div className="join-code-hero"><LogIn size={21} /><div><strong>Enter your 7-character classroom code</strong><span>Codes are case-insensitive.</span></div></div>
      <Field label="Join code"><input autoFocus className="code-input" value={joinCode} onChange={(e) => setJoinCode(e.target.value.replace(/\s/g, '').toUpperCase())} maxLength={32} placeholder="A1B2C3D" /></Field>
      <div className="modal-actions"><Button variant="ghost" onClick={() => close('join')}>Cancel</Button><Button onClick={join} disabled={busy || joinCode.trim().length < 4}>{busy ? 'Joining…' : 'Join classroom'}</Button></div>
    </Modal>}
  </Page>;
}
