import type {
  ApiErrorShape, LiveLanguage, LiveWarmResult,
  Group,
  Lang,
  LessonPack,
  LessonSummary,
  User,
  VersionSummary,
} from '../types';

const BASE = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api/v1').replace(/\/$/, '');

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;
  retryable: boolean;
  requestId?: string;

  constructor(status: number, shape: ApiErrorShape | string) {
    const normalized: ApiErrorShape =
      typeof shape === 'string' ? { message: shape } : shape;
    super(normalized.message || 'Request failed.');
    this.name = 'ApiError';
    this.status = status;
    this.code = normalized.code || 'REQUEST_FAILED';
    this.details = normalized.details;
    this.retryable = Boolean(normalized.retryable);
    this.requestId = normalized.request_id;
  }
}

function token() {
  return localStorage.getItem('bhashasaathi_token');
}

async function parseResponse(response: Response): Promise<any> {
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    try {
      return await response.json();
    } catch {
      return null;
    }
  }
  const text = await response.text();
  return text || null;
}

function normalizeError(status: number, data: any): ApiError {
  const detail = data?.detail ?? data ?? {};
  if (typeof detail === 'string') return new ApiError(status, detail);
  return new ApiError(status, {
    code: detail?.code || data?.code || `HTTP_${status}`,
    message: detail?.message || data?.message || `Request failed (${status}).`,
    details: detail?.details ?? data?.details,
    retryable: detail?.retryable ?? data?.retryable ?? status >= 500,
    request_id: data?.request_id,
  });
}

async function request<T>(path: string, options: RequestInit = {}, timeoutMs = 20000): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !headers.has('Content-Type') && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }
  const accessToken = token();
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`);

  const controller = new AbortController();
  const parentSignal = options.signal;
  let abortTimer: number | null = null;
  let timedOut = false;
  const abortFromParent = () => controller.abort(parentSignal?.reason);

  if (parentSignal) {
    if (parentSignal.aborted) controller.abort(parentSignal.reason);
    else parentSignal.addEventListener('abort', abortFromParent, { once: true });
  }
  abortTimer = window.setTimeout(() => {
    timedOut = true;
    controller.abort(new DOMException('Request timed out.', 'TimeoutError'));
  }, timeoutMs);

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, { ...options, headers, signal: controller.signal });
  } catch (error) {
    if (timedOut) {
      throw new ApiError(408, {
        code: 'REQUEST_TIMEOUT',
        message: `The request took longer than ${Math.round(timeoutMs / 1000)} seconds. The lesson is safe; try again.`,
        retryable: true,
      });
    }
    if (controller.signal.aborted) throw error;
    throw new ApiError(0, {
      code: 'NETWORK_ERROR',
      message: 'Cannot reach the BhashaSaathi API. Start FastAPI on port 8000 and try again.',
      retryable: true,
    });
  } finally {
    if (abortTimer !== null) window.clearTimeout(abortTimer);
    if (parentSignal) parentSignal.removeEventListener('abort', abortFromParent);
  }

  const data = await parseResponse(response);
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/login' && path !== '/auth/register') {
      window.dispatchEvent(new CustomEvent('bhashasaathi:session-expired'));
    }
    throw normalizeError(response.status, data);
  }
  return data as T;
}

export const api = {
  base: BASE,
  login: (email: string, password: string) =>
    request<{ access_token: string; user: User }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email: email.trim().toLowerCase(), password }),
    }),
  register: (body: { name: string; email: string; password: string }) =>
    request<{ access_token: string; user: User }>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ ...body, email: body.email.trim().toLowerCase() }),
    }),
  logout: () => request<{ ok: boolean }>('/auth/logout', { method: 'POST' }),
  me: () => request<User>('/auth/me'),

  groups: () => request<Group[]>('/groups'),
  group: (id: number) => request<Group>(`/groups/${id}`),
  members: (id: number) => request<Array<{ id: number; user_id: number; name: string; email: string; role: string; joined_at: string }>>(`/groups/${id}/members`),
  createGroup: (body: { name: string; grade: string; subject: string }) =>
    request<Group>('/groups', { method: 'POST', body: JSON.stringify(body) }),
  joinGroup: (join_code: string) =>
    request<{ already_member: boolean; group: Group }>('/groups/join', {
      method: 'POST',
      body: JSON.stringify({ join_code: join_code.trim().toUpperCase() }),
    }),

  lessons: (groupId: number) => request<LessonSummary[]>(`/groups/${groupId}/lessons`),
  createLesson: (groupId: number, body: { title: string; source_text: string; source_language: Lang; grade: string; subject: string; topic: string }) =>
    request<LessonSummary>(`/groups/${groupId}/lessons`, { method: 'POST', body: JSON.stringify(body) }),
  lesson: (id: number) => request<LessonPack>(`/lessons/${id}`),
  patchLesson: (id: number, body: Record<string, unknown>) =>
    request<LessonPack>(`/lessons/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteLesson: (id: number) => request<{ ok: boolean }>(`/lessons/${id}`, { method: 'DELETE' }),
  analyze: (id: number) => request<any>(`/lessons/${id}/analyze`, { method: 'POST' }),
  prepareConcepts: (id: number) => request<any>(`/lessons/${id}/prepare-concepts`, { method: 'POST' }),
  translate: (id: number, target_language: Lang, source_text?: string, clean_transcript?: string, source_language?: Lang) =>
    request<any>(`/lessons/${id}/translate`, { method: 'POST', body: JSON.stringify({ target_language, source_text, clean_transcript, source_language }) }, 90000),
  verify: (id: number, body: { translation_id: number; approved: boolean; edited_text?: string; native_review_status?: 'NOT_REVIEWED' | 'PENDING' | 'REJECTED' }) =>
    request<any>(`/lessons/${id}/verify-translation`, { method: 'POST', body: JSON.stringify(body) }),
  saveTemporaryTranslation: (id: number, body: { translation_id: number; edited_text?: string }) =>
    request<any>(`/lessons/${id}/translation/${body.translation_id}/temporary`, { method: 'POST', body: JSON.stringify({ edited_text: body.edited_text || '' }) }),
  backTranslate: (id: number, translationId: number) =>
    request<{ translation_id: number; back_translation: string; cached: boolean; back_translation_ms: number }>(`/lessons/${id}/translation/${translationId}/back-translate`, { method: 'POST' }, 90000),
  preparations: (id: number) => request<any[]>(`/lessons/${id}/preparations`),
  retryPreparation: (id: number, jobId: number) => request<any>(`/lessons/${id}/preparations/${jobId}/retry`, { method: 'POST' }),
  queueTTSPreparation: (id: number, language: Lang) => request<any>(`/lessons/${id}/preparations/tts/${language}`, { method: 'POST' }),
  tts: (id: number, target_language: Lang) =>
    request<any>(`/lessons/${id}/tts`, { method: 'POST', body: JSON.stringify({ target_language, speaker: 'classroom_teacher' }) }),
  practice: (id: number, language: Lang, count = 4) =>
    request<any[]>(`/lessons/${id}/generate-practice`, { method: 'POST', body: JSON.stringify({ language, count }) }, 90000),
  approvePractice: (id: number, questionId: number, approved: boolean) =>
    request<any>(`/lessons/${id}/practice/${questionId}/approve`, { method: 'POST', body: JSON.stringify({ approved }) }),
  worksheet: (id: number) => request<{ html: string; [key: string]: unknown }>(`/lessons/${id}/generate-worksheet`, { method: 'POST' }),
  publish: (id: number) => request<LessonPack>(`/lessons/${id}/publish`, { method: 'POST' }),
  versions: (id: number) => request<VersionSummary[]>(`/lessons/${id}/versions`),
  newVersion: (id: number, body: Record<string, unknown>) =>
    request<LessonPack>(`/lessons/${id}/versions`, { method: 'POST', body: JSON.stringify(body) }),
  rollback: (id: number, version_id: number) => request<LessonPack>(`/lessons/${id}/rollback?version_id=${version_id}`, { method: 'POST' }),
  transcribe: (id: number, file: File) => {
    const form = new FormData();
    form.append('file', file, file.name);
    return request<{ text: string; language: string; segments?: unknown[] }>(`/lessons/${id}/transcribe`, { method: 'POST', body: form });
  },

  artifact: (id: number) => `${BASE}/artifacts/${id}`,
  fetchArtifact: async (id: number) => {
    const accessToken = token();
    const response = await fetch(`${BASE}/artifacts/${id}`, {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined,
    });
    if (!response.ok) throw normalizeError(response.status, await parseResponse(response));
    return response.blob();
  },

  studentLessons: () => request<LessonPack[]>('/student/lessons'),
  studentPackage: (id: number) => request<LessonPack>(`/student/lessons/${id}/package`),
  attempt: (body: { lesson_id: number; lesson_version_id: number; language: Lang; answers: Record<string, unknown> }) =>
    request<any>('/student/attempts', { method: 'POST', body: JSON.stringify(body) }),
  translationFeedback: (translationId: number, status: 'CONFIRMED' | 'REPORTED', comment = '') =>
    request<any>(`/student/translations/${translationId}/feedback`, { method: 'POST', body: JSON.stringify({ status, comment }) }),
  progress: () => request<any>('/student/progress'),

  syncPush: (device_id: string, operations: any[]) =>
    request<any>('/sync/push', { method: 'POST', body: JSON.stringify({ device_id, operations }) }),
  syncPull: () => request<any>('/sync/pull', { method: 'POST', body: JSON.stringify({}) }),

  health: () => request<any>('/health'),
  modelHealth: () => request<any>('/health/models'),
  liveLanguages: () => request<LiveLanguage[]>('/live/languages'),
  warmLive: (source_language: Lang, target_language: Lang) =>
    request<LiveWarmResult>('/live/warm', { method: 'POST', body: JSON.stringify({ source_language, target_language }) }),
  warmTranslation: (source_language: Lang, target_language: Lang) =>
    request<{ source: Lang; target: Lang; route: string; warm_ms: number }>('/runtime/warm-translation', { method: 'POST', body: JSON.stringify({ source_language, target_language }) }),
  warmTTS: () =>
    request<{ ready: boolean; warm_ms: number }>('/runtime/warm-tts', { method: 'POST' }),
};

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'PUBLISH_BLOCKED' && error.details && typeof error.details === 'object') {
      const problems = (error.details as any).problems;
      if (Array.isArray(problems) && problems.length) {
        return `${error.message} ${problems.map((item: string) => item.replaceAll('_', ' ').replaceAll(':', ': ')).join(' • ')}`;
      }
    }
    if (error.details && typeof error.details === 'object') {
      const runtime = (error.details as any).runtime_error;
      if (typeof runtime === 'string' && runtime.trim()) {
        return `${error.message} Technical detail: ${runtime.trim()}`;
      }
    }
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return 'Something went wrong. Please try again.';
}
