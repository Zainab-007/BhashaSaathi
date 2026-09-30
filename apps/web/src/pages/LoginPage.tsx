import { FormEvent, useState } from 'react';
import { BookOpen, CheckCircle2, Languages, ShieldCheck, WifiOff } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../features/auth/AuthContext';
import { Button, Card, Field, Status } from '../components/UI';
import { errorMessage } from '../lib/api';

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState('teacher@bhashasaathi.local');
  const [password, setPassword] = useState('Demo@1234');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(''); setBusy(true);
    try {
      const user = await login(email, password);
      navigate('/dashboard', { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally { setBusy(false); }
  };

  return <div className="auth-page">
    <section className="auth-story-side">
      <div className="auth-brand"><div className="brand-mark large">भा</div><div><strong>BhashaSaathi</strong><span>Translate the classroom, not just the words.</span></div></div>
      <div className="auth-story-content">
        <div className="auth-kicker">LOCAL • TEACHER-FIRST • OFFLINE READY</div>
        <h1>One teaching input.<br /><em>One learner-ready lesson.</em></h1>
        <p>Turn a Hindi teaching moment into a structured, reviewed, multilingual classroom experience without handing the final decision to the AI.</p>
        <div className="story-points"><div><CheckCircle2 /><span>Teacher-controlled publishing</span></div><div><Languages /><span>Hindi · English · Marathi / Devanagari</span></div><div><WifiOff /><span>Published lessons keep working offline</span></div></div>
      </div>
      <div className="auth-foot">BhashaSaathi · SIH26042 prototype</div>
    </section>
    <section className="auth-form-side"><Card className="auth-card">
      <div className="auth-logo"><BookOpen size={18} /> BhashaSaathi</div>
      <div className="auth-card-title"><div className="eyebrow">WELCOME BACK</div><h2>Sign in to your classroom</h2><p>Continue where you left off.</p></div>
      {error && <Status tone="danger">{error}</Status>}
      <form onSubmit={submit} className="auth-form">
        <Field label="Email"><input autoComplete="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></Field>
        <Field label="Password"><input autoComplete="current-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
        <Button type="submit" disabled={busy} className="full">{busy ? 'Signing in…' : 'Sign in'}</Button>
      </form>
    {/*  <div className="demo-box"><strong>Demo access</strong><span>Teacher: <b>teacher@bhashasaathi.local</b></span><span>Password: <b>Demo@1234</b></span></div>*/}
      <div className="auth-switch"><span>New to BhashaSaathi?</span><button onClick={() => navigate('/register')}>Create your flexible account →</button></div>
      <div className="trust-note"><ShieldCheck size={16} /><span>No paid AI runtime dependency. Your local models stay on your machine.</span></div>
    </Card></section>
  </div>;
}
