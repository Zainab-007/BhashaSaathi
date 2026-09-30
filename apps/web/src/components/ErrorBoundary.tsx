import { Component, type ErrorInfo, type ReactNode } from 'react';

type Props = { children: ReactNode };
type State = { hasError: boolean; message: string };

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, message: '' };
  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, message: error?.message || 'Unexpected interface error.' };
  }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error('BhashaSaathi UI error', error, info); }
  render() {
    if (!this.state.hasError) return this.props.children;
    return <div className="loading-screen" role="alert"><div className="loading-brand"><div className="brand-mark">भा</div><strong>BhashaSaathi recovered from a page error</strong><span>{this.state.message}</span><button className="btn btn-secondary" onClick={() => window.location.reload()}>Reload page</button></div></div>;
  }
}
