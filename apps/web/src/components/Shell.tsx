import { useEffect, useState, type ReactNode } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { Activity, AudioLines, BookOpen, ChevronDown, GraduationCap, LayoutDashboard, LogOut, Menu, School, Users, WifiOff, X } from 'lucide-react';
import { useAuth } from '../features/auth/AuthContext';
import { LANGS, type Lang } from '../types';

export function Shell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [language, setLanguage] = useState<Lang>(() => (localStorage.getItem('bhashasaathi_language') as Lang) || user?.preferred_language || 'eng_Latn');
  const navigate = useNavigate();

  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener('resize', close);
    return () => window.removeEventListener('resize', close);
  }, []);

  const setPreferredLanguage = (next: Lang) => {
    setLanguage(next); localStorage.setItem('bhashasaathi_language', next); localStorage.setItem('student_language', next);
    window.dispatchEvent(new CustomEvent('bhashasaathi:language-changed', { detail: next }));
  };

  const links = [
    { to: '/dashboard', label: 'Workspace', Icon: LayoutDashboard },
    { to: '/groups', label: 'Classrooms', Icon: Users },
    { to: '/student', label: 'My Learning', Icon: GraduationCap },
    { to: '/offline', label: 'Offline Center', Icon: WifiOff },
    { to: '/diagnostics', label: 'Diagnostics', Icon: Activity },
    { to: '/live', label: 'Live Translate', Icon: AudioLines },
  ];

  const signOut = async () => { await logout(); navigate('/login', { replace: true }); };

  return <div className="app-shell">
    <aside className={`sidebar ${open ? 'sidebar-open' : ''}`}>
      <div className="side-brand">
        <div className="brand-mark">भा</div>
        <div className="side-brand-copy"><strong>BhashaSaathi</strong><span>Translate the classroom, not just the words.</span></div>
        <button className="mobile-close" onClick={() => setOpen(false)} aria-label="Close navigation"><X size={18} /></button>
      </div>
      <div className="role-badge"><School size={12} /> TEACH + LEARN</div>
      <nav className="side-nav">
        {links.map(({ to, label, Icon }) => <NavLink key={to} to={to} onClick={() => setOpen(false)} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}><Icon size={18} /><span>{label}</span></NavLink>)}
      </nav>
      <div className="side-bottom">
        <div className="language-control">
          <label htmlFor="workspace-language">Language</label>
          <div className="language-select-wrap"><LanguagesIcon /><select id="workspace-language" value={language} onChange={(e) => setPreferredLanguage(e.target.value as Lang)}>{Object.entries(LANGS).map(([code, info]) => <option key={code} value={code}>{info.native} · {info.name}</option>)}</select><ChevronDown size={14} /></div>
        </div>
        <div className="user-mini"><div className="avatar">{user?.name?.slice(0, 1).toUpperCase()}</div><div><strong>{user?.name}</strong><span>Flexible classroom account</span></div></div>
        <button className="logout-btn" onClick={signOut}><LogOut size={16} /> Sign out</button>
      </div>
    </aside>
    {open && <button className="scrim" aria-label="Close navigation" onClick={() => setOpen(false)} />}
    <main className="main">
      <div className="mobile-topbar"><button className="menu-btn" onClick={() => setOpen(true)} aria-label="Open navigation"><Menu size={21} /></button><div className="mobile-brand"><div className="brand-mark small">भा</div><strong>BhashaSaathi</strong></div><div className="mobile-language"><select value={language} onChange={(e) => setPreferredLanguage(e.target.value as Lang)} aria-label="Language">{Object.entries(LANGS).map(([code, info]) => <option key={code} value={code}>{info.native}</option>)}</select></div></div>
      {children}
    </main>
  </div>;
}

function LanguagesIcon() { return <span className="language-icon"><BookOpen size={14} /></span>; }

export function Page({ eyebrow, title, subtitle, actions, children }: { eyebrow?: string; title: string; subtitle?: string; actions?: ReactNode; children: ReactNode }) {
  return <div className="page"><header className="page-head"><div className="page-heading"><div className="eyebrow">{eyebrow}</div><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div>{actions && <div className="head-actions">{actions}</div>}</header>{children}</div>;
}

export function Breadcrumbs({ items }: { items: Array<{ label: string; href?: string }> }) {
  return <div className="breadcrumbs">{items.map((item, index) => <span key={`${item.label}-${index}`}>{item.href ? <a href={item.href}>{item.label}</a> : item.label}{index < items.length - 1 && <b>›</b>}</span>)}</div>;
}

export function SectionTitle({ title, subtitle, action }: { title: string; subtitle?: string; action?: ReactNode }) {
  return <div className="section-title"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>{action}</div>;
}
