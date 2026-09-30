import { all, del, put } from './idb';
import type { Lang } from '../types';

export interface LiveTurn {
  id: string;
  sequence: number;
  transcript: string;
  translation: string;
  audio?: Blob;
  createdAt: number;
  totalMs?: number;
}

export interface LiveHistoryEntry {
  id: string;
  source: Lang;
  target: Lang;
  startedAt: number;
  endedAt: number;
  saved: boolean;
  expiresAt: number;
  turns: LiveTurn[];
}

const TTL = 24 * 60 * 60 * 1000;

export async function cleanupLiveHistory() {
  const entries = await all<LiveHistoryEntry>('live_history');
  const now = Date.now();
  await Promise.all(entries.filter((entry) => !entry.saved && entry.expiresAt <= now).map((entry) => del('live_history', entry.id)));
}

export async function listLiveHistory() {
  await cleanupLiveHistory();
  const entries = await all<LiveHistoryEntry>('live_history');
  return entries.sort((a, b) => b.startedAt - a.startedAt);
}

export async function saveLiveSession(entry: Omit<LiveHistoryEntry, 'expiresAt' | 'saved'> & { saved?: boolean }) {
  const saved = Boolean(entry.saved);
  const value: LiveHistoryEntry = {
    ...entry,
    saved,
    expiresAt: saved ? Number.MAX_SAFE_INTEGER : Date.now() + TTL,
  };
  await put('live_history', value.id, value);
  return value;
}

export async function setLiveSaved(id: string, saved: boolean) {
  const entries = await all<LiveHistoryEntry>('live_history');
  const current = entries.find((entry) => entry.id === id);
  if (!current) return;
  await put('live_history', id, {
    ...current,
    saved,
    expiresAt: saved ? Number.MAX_SAFE_INTEGER : Date.now() + TTL,
  });
}

export async function deleteLiveSession(id: string) {
  await del('live_history', id);
}
