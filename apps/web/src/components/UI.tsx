import { CheckCircle2, AlertTriangle, Info, LoaderCircle, X, Check, Copy } from 'lucide-react';
import { createContext, useContext, useMemo, useState, type FormEvent, type ReactNode } from 'react';

export function Button({
  children,
  onClick,
  type = 'button',
  variant = 'primary',
  disabled = false,
  className = '',
  size,
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  type?: 'button' | 'submit';
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  disabled?: boolean;
  className?: string;
  /** Optional size modifier class suffix, e.g. "lg" → adds btn-lg CSS class */
  size?: string;
  /** Accessible tooltip / title attribute */
  title?: string;
}) {
  const sizeClass = size ? ` btn-${size}` : '';
  return <button type={type} onClick={onClick} disabled={disabled} title={title} className={`btn btn-${variant}${sizeClass} ${className}`}>{children}</button>;
}

export function Card({ children, className = '', onClick }: { children: ReactNode; className?: string; onClick?: () => void }) {
  return <section className={`card ${className}`} onClick={onClick}>{children}</section>;
}

export function Field({ label, children, hint, error }: { label: string; children: ReactNode; hint?: string; error?: string }) {
  return <label className={`field ${error ? 'field-error' : ''}`}>
    <span>{label}</span>
    {children}
    {error ? <small className="field-error-text">{error}</small> : hint ? <small>{hint}</small> : null}
  </label>;
}

export function Status({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'info' }) {
  return <span className={`status status-${tone}`}>
    {tone === 'success' ? <CheckCircle2 size={14} /> : tone === 'warning' || tone === 'danger' ? <AlertTriangle size={14} /> : <Info size={14} />}
    {children}
  </span>;
}

export function Busy({ label = 'Working…' }: { label?: string }) {
  return <div className="busy"><LoaderCircle size={18} className="spin" />{label}</div>;
}

export function Empty({ title, body, action }: { title: string; body: string; action?: ReactNode }) {
  return <div className="empty"><div className="empty-icon"><Info size={18} /></div><h3>{title}</h3><p>{body}</p>{action}</div>;
}

export function Divider() { return <div className="divider" />; }

export function Modal({ title, children, onClose, wide = false }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div className={`modal ${wide ? 'modal-wide' : ''}`} role="dialog" aria-modal="true" aria-label={title}>
      <div className="modal-head"><div><strong>{title}</strong><span>Press Escape or close when you're done.</span></div><button className="icon-btn" onClick={onClose} aria-label="Close"><X size={18} /></button></div>
      <div className="modal-body">{children}</div>
    </div>
  </div>;
}

interface Toast { id: string; tone: 'success' | 'error' | 'info'; message: string }
interface ToastContext { push: (message: string, tone?: Toast['tone']) => void }
const Toasts = createContext<ToastContext | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const value = useMemo<ToastContext>(() => ({
    push(message, tone = 'success') {
      const id = crypto.randomUUID();
      setItems((old) => [...old, { id, message, tone }]);
      window.setTimeout(() => setItems((old) => old.filter((item) => item.id !== id)), 4200);
    },
  }), []);
  return <Toasts.Provider value={value}>{children}<div className="toast-stack">{items.map((item) => <div key={item.id} className={`toast toast-${item.tone}`}><div>{item.tone === 'success' ? <Check size={16} /> : item.tone === 'error' ? <AlertTriangle size={16} /> : <Info size={16} />}</div><span>{item.message}</span><button onClick={() => setItems((old) => old.filter((x) => x.id !== item.id))}><X size={14} /></button></div>)}</div></Toasts.Provider>;
}

export function useToast() {
  const ctx = useContext(Toasts);
  if (!ctx) throw new Error('ToastProvider is missing.');
  return ctx;
}

export function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const { push } = useToast();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      push('Copied to clipboard.');
    } catch {
      push('Clipboard is unavailable in this browser.', 'error');
    }
  };
  return <Button variant="ghost" onClick={copy}><Copy size={15} /> {label}</Button>;
}

export function Form({ children, onSubmit }: { children: ReactNode; onSubmit: (event: FormEvent<HTMLFormElement>) => void }) {
  return <form onSubmit={onSubmit}>{children}</form>;
}
