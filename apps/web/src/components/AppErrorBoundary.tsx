import { Component, type ErrorInfo, type ReactNode } from 'react';

type Props = { children: ReactNode };
type State = { error: Error | null };

export class AppErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('BhashaSaathi UI error', error, info);
  }

  private reload = () => window.location.reload();

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="loading-screen">
        <div className="loading-brand" style={{ maxWidth: 560, gap: 12 }}>
          <div className="brand-mark">भा</div>
          <strong>Something went wrong in this screen</strong>
          <span>The lesson data is still stored on the local server. Reloading is safe.</span>
          <button className="btn btn-primary" onClick={this.reload}>Reload workspace</button>
          <small style={{ opacity: 0.7 }}>{this.state.error.message}</small>
        </div>
      </div>
    );
  }
}
