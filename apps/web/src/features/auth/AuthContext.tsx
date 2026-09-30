import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api } from '../../lib/api';
import type { User } from '../../types';

interface ContextValue {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<User>;
  register: (body: { name: string; email: string; password: string }) => Promise<User>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const Auth = createContext<ContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    try {
      setUser(await api.me());
    } catch {
      localStorage.removeItem('bhashasaathi_token');
      setUser(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const onExpired = () => {
      localStorage.removeItem('bhashasaathi_token');
      setUser(null);
    };
    window.addEventListener('bhashasaathi:session-expired', onExpired);
    if (localStorage.getItem('bhashasaathi_token')) void refresh();
    else setLoading(false);
    return () => window.removeEventListener('bhashasaathi:session-expired', onExpired);
  }, []);

  const value = useMemo<ContextValue>(() => ({
    user,
    loading,
    login: async (email, password) => {
      const result = await api.login(email, password);
      localStorage.setItem('bhashasaathi_token', result.access_token);
      setUser(result.user);
      return result.user;
    },
    register: async (body) => {
      const result = await api.register(body);
      localStorage.setItem('bhashasaathi_token', result.access_token);
      setUser(result.user);
      return result.user;
    },
    logout: async () => {
      try { await api.logout(); } catch { /* local sign-out still succeeds */ }
      localStorage.removeItem('bhashasaathi_token');
      setUser(null);
    },
    refresh,
  }), [user, loading]);

  return <Auth.Provider value={value}>{children}</Auth.Provider>;
}

export function useAuth() {
  const context = useContext(Auth);
  if (!context) throw new Error('AuthProvider is missing.');
  return context;
}
