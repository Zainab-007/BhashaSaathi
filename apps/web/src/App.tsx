import { Navigate, Route, Routes } from 'react-router-dom';
import type { ReactNode } from 'react';
import { useAuth } from './features/auth/AuthContext';
import { Shell } from './components/Shell';
import LoginPage from './pages/LoginPage';
import LandingPage from './pages/LandingPage';
import RegisterPage from './pages/RegisterPage';
import AdminDashboardPage from './pages/AdminDashboardPage';
import GroupsPage from './pages/GroupsPage';
import GroupPage from './pages/GroupPage';
import LessonBuilderPage from './pages/LessonBuilderPage';
import StudentHomePage from './pages/StudentHomePage';
import StudentLessonPage from './pages/StudentLessonPage';
import OfflinePage from './pages/OfflinePage';
import DiagnosticsPage from './pages/DiagnosticsPage';
import NotFoundPage from './pages/NotFoundPage';
import LiveTranslatePage from './pages/LiveTranslatePage';
import ErrorBoundary from './components/ErrorBoundary';

function Protected({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="loading-screen"><div className="loading-brand"><div className="brand-mark">भा</div><strong>Loading BhashaSaathi</strong><span>Preparing your classroom workspace…</span></div></div>;
  return user ? <Shell>{children}</Shell> : <Navigate to="/login" replace />;
}


export default function App() {
  return <ErrorBoundary><Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route path="/register" element={<RegisterPage />} />
    <Route path="/" element={<LandingPage />} />
    <Route path="/live" element={<LiveTranslatePage />} />
    <Route path="/dashboard" element={<Protected><AdminDashboardPage /></Protected>} />
    <Route path="/groups" element={<Protected><GroupsPage /></Protected>} />
    <Route path="/groups/:id" element={<Protected><GroupPage /></Protected>} />
    <Route path="/lessons/:id/builder" element={<Protected><LessonBuilderPage /></Protected>} />
    <Route path="/student" element={<Protected><StudentHomePage /></Protected>} />
    <Route path="/student/lessons/:id" element={<Protected><StudentLessonPage /></Protected>} />
    <Route path="/practice/:id" element={<Protected><StudentLessonPage /></Protected>} />
    <Route path="/offline" element={<Protected><OfflinePage /></Protected>} />
    <Route path="/diagnostics" element={<Protected><DiagnosticsPage /></Protected>} />
    <Route path="*" element={<NotFoundPage />} />
  </Routes></ErrorBoundary>;
}
