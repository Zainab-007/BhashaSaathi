export type Lang = 'eng_Latn' | 'hin_Deva' | 'mar_Deva' | 'sat_Olck';
export type Role = 'user' | 'admin' | 'student';

export const LANGS: Record<Lang, { name: string; native: string; script: string }> = {
  eng_Latn: { name: 'English', native: 'English', script: 'Latin' },
  hin_Deva: { name: 'Hindi', native: 'हिन्दी', script: 'Devanagari' },
  mar_Deva: { name: 'Marathi', native: 'मराठी', script: 'Devanagari' },
  sat_Olck: { name: 'Santali', native: 'ᱥᱟᱱᱛᱟᱲᱤ', script: 'Ol Chiki' },
};

export interface User {
  id: number;
  name: string;
  email: string;
  preferred_language: Lang;
  account_role: Role;
  capabilities?: { create_classroom: boolean; join_classroom: boolean };
}

export interface Group {
  id: number;
  name: string;
  join_code: string;
  grade: string;
  subject: string;
  owner_id: number;
  members: number;
  lessons: number;
  created_at: string;
}

export interface LessonSummary {
  id: number;
  group_id: number;
  title: string;
  grade: string;
  subject: string;
  topic: string;
  source_language: Lang;
  status: string;
  current_version_id: number | null;
  version_number: number | null;
  updated_at: string | null;
}

export interface Concept {
  concept_id: string;
  label: string;
  source_span?: string;
  definition?: string;
  keywords?: string[];
  importance?: string;
}

export interface Translation {
  id: number;
  language: Lang;
  text: string;
  confidence: number;
  flags: string[];
  validation: {
    confidence?: number;
    concepts_missing?: string[];
    concepts_extra?: string[];
    glossary_violations?: string[];
    script_anomaly?: boolean;
    number_mismatch?: boolean;
    source_leakage?: boolean;
    publish_blocked?: boolean;
    [key: string]: unknown;
  };
  verification_status: string;
  teacher_edited: boolean;
  native_review_status: string;
  student_feedback?: { confirmed: number; reported: number };
  my_student_feedback?: 'CONFIRMED' | 'REPORTED' | null;
}

export interface Practice {
  id: number;
  concept_id: string;
  prompt: string;
  options: string[];
  correct_answer: string;
  explanation: string;
  language: Lang;
  question_type?: string;
  approved?: boolean;
}

export interface Artifact {
  id: number;
  type: string;
  language: Lang;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  url: string;
}

export interface LessonVersion {
  id: number;
  version_number: number;
  source_text: string;
  clean_transcript: string;
  learning_objectives: string[];
  concepts: Concept[];
  ai_explanation: Record<string, unknown>;
  activities: string[];
  approved_at: string | null;
  published_at: string | null;
}

export interface LessonPack {
  lesson: LessonSummary;
  worksheet_html?: string;
  worksheet_meta?: Record<string, unknown>;
  version: LessonVersion;
  translations: Translation[];
  artifacts: Artifact[];
  practice: Practice[];
  practice_all?: Practice[];
  preparations?: Array<{ id: number; kind: string; language: Lang; status: 'QUEUED' | 'RUNNING' | 'READY' | 'FAILED'; error?: string | null; created_at?: string | null; started_at?: string | null; finished_at?: string | null }>;
}

export interface VersionSummary {
  id: number;
  version_number: number;
  created_at: string;
  published_at: string | null;
  approved_at: string | null;
}

export interface ApiErrorShape {
  code?: string;
  message?: string;
  details?: unknown;
  retryable?: boolean;
  request_id?: string;
}

export interface LiveLanguage { code: Lang; name: string; native: string; whisper?: string }
export interface LiveWarmResult { source: Lang; target: Lang; warm_ms: number; [key: string]: unknown }
