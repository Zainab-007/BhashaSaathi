import { useEffect, useMemo, useState } from 'react';
import { CheckCircle2, Clipboard, RefreshCw, TriangleAlert } from 'lucide-react';
import { Page, SectionTitle } from '../components/Shell';
import { Button, Card, Status, useToast } from '../components/UI';
import { api, errorMessage } from '../lib/api';

export default function DiagnosticsPage() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { push } = useToast();

  const load = async () => {
    setLoading(true);
    try {
      const [health, models] = await Promise.all([api.health(), api.modelHealth()]);
      setData({ health, models });
      setError('');
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const translationReady = useMemo(() => {
    const state = data?.models?.translation;
    return Boolean(state && state['indic-indic'] && state['en-indic'] && state['indic-en']);
  }, [data]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
      push('Diagnostics copied.');
    } catch {
      push('Clipboard is unavailable.', 'error');
    }
  };

  return <Page eyebrow="ADMIN / DEVELOPER" title="Diagnostics" subtitle="Verify the exact local model paths, dependency runtime and API health before the demo." actions={<Button onClick={() => void load()} disabled={loading}><RefreshCw size={15} className={loading ? 'spin' : ''} /> Refresh</Button>}>
    {error && <Card className="error-card"><Status tone="danger">{error}</Status></Card>}
    {data && <div className="content-grid two-thirds">
      <Card>
        <SectionTitle title="Application health" />
        <div className="diag-list">
          <div><span>API status</span><Status tone="success">{data.health.status}</Status></div>
          <div><span>Product</span><strong>{data.health.product}</strong></div>
          <div><span>Mode</span><strong>{data.health.mode}</strong></div>
          <div><span>Version</span><strong>{data.health.version}</strong></div>
          <div><span>Model root</span><code>{data.models.model_root}</code></div>
          <div><span>Device</span><strong>{data.models.device}</strong></div>
        </div>
      </Card>
      <Card>
        <SectionTitle title="Local AI runtime" subtitle="A folder being present is not the same as a runtime being ready." />
        <div className="diag-list">
          <div><span>IndicTrans2</span><Status tone={translationReady ? 'success' : 'danger'}>{translationReady ? 'Ready' : 'Check folders'}</Status></div>
          <div><span>Whisper Small</span><Status tone={data.models.stt ? 'success' : 'danger'}>{data.models.stt ? 'Ready' : 'Missing'}</Status></div>
          <div><span>Parler model</span><Status tone={data.models.tts?.model ? 'success' : 'danger'}>{data.models.tts?.model ? 'Present' : 'Missing'}</Status></div>
          <div><span>Prompt tokenizer</span><Status tone={data.models.tts?.prompt_tokenizer ? 'success' : 'warning'}>{data.models.tts?.prompt_tokenizer ? 'Ready' : 'Missing'}</Status></div>
          <div><span>FLAN-T5 description tokenizer</span><Status tone={data.models.tts?.description_tokenizer ? 'success' : 'warning'}>{data.models.tts?.description_tokenizer ? 'Ready' : 'Missing'}</Status></div>
          <div><span>Feature extractor</span><Status tone={data.models.tts?.feature_extractor ? 'success' : 'warning'}>{data.models.tts?.feature_extractor ? 'Ready' : 'Missing'}</Status></div>
          <div><span>Parler runtime</span><Status tone={data.models.tts?.runtime_ready ? 'success' : 'warning'}>{data.models.tts?.runtime_ready ? 'Ready' : 'Blocked by support assets'}</Status></div>
        </div>
        {!data.models.tts?.runtime_ready && data.models.tts?.model && <div className="tip-box"><div className="button-row"><TriangleAlert size={17} /><strong>Finish TTS local support</strong></div><p>From the repository root run <code>python scripts\download_tts_support.py</code>. This provisions only the FLAN-T5 tokenizer/config assets required by the Parler checkpoint; it does not download the full FLAN-T5 weights.</p><p>Tokenizer path: <code>{data.models.tts?.description_tokenizer_path}</code></p></div>}
      </Card>
    </div>}
    <Card>
      <SectionTitle title="Demo checklist" subtitle="These are the real product stages that must be demonstrated live." action={<Button variant="ghost" onClick={copy}><Clipboard size={15} /> Copy diagnostics</Button>} />
      <div className="demo-check-grid">
        {[
          'Login', 'Create classroom', 'Create lesson', 'Whisper transcript', 'Concept extraction',
          'Hindi → English', 'Hindi → Marathi', 'Translation Guard', 'Teacher approval', 'Marathi TTS',
          'Worksheet + prepared practice', 'Publish package', 'Offline lesson', 'Offline practice + sync',
        ].map((item) => <div key={item}><CheckCircle2 /> {item}</div>)}
      </div>
      <div className="footer-note">Do not label an AI-generated Marathi translation as automatically verified without qualified language review. Do not claim zero translation errors or guaranteed on-device generative AI on low-end Android.</div>
    </Card>
    {data?.models?.last_error && <Card className="error-card"><SectionTitle title="Last model error" subtitle="The backend stores the latest isolated model failure to make debugging actionable." /><pre className="json-view">{JSON.stringify(data.models.last_error, null, 2)}</pre></Card>}
  </Page>;
}
