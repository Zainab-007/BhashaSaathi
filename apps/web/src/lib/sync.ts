import { all, put } from './idb';
import { api } from './api';

const DEVICE_KEY = 'bhashasaathi-device';

export function deviceId() {
  let id = localStorage.getItem(DEVICE_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(DEVICE_KEY, id);
  }
  return id;
}

export async function queueAttempt(payload: Record<string, unknown>) {
  const queueId = crypto.randomUUID();
  await put('sync_queue', queueId, { queueId, type: 'attempt', payload, createdAt: Date.now(), state: 'pending', attempts: 0 });
}

export async function pendingCount() {
  return (await all<any>('sync_queue')).filter((item) => item.state === 'pending').length;
}

export async function syncNow() {
  if (!navigator.onLine) return { ok: false, count: await pendingCount() };
  const rows = (await all<any>('sync_queue')).filter((item) => item.state === 'pending');
  if (!rows.length) return { ok: true, count: 0 };

  try {
    const result = await api.syncPush(
      deviceId(),
      rows.map((row) => ({
        operation: 'ATTEMPT_UPSERT',
        entity_type: 'attempt',
        entity_id: row.payload?.localId || row.queueId,
        payload: row.payload,
      })),
    );
    const failures = new Set((result.failures || []).map((item: any) => String(item.entity_id)));
    for (const row of rows) {
      const entityId = String(row.payload?.localId || row.queueId);
      if (failures.has(entityId)) {
        await put('sync_queue', row.queueId, { ...row, attempts: (row.attempts || 0) + 1, state: 'pending', lastError: failures.has(entityId) ? 'Server rejected this offline attempt.' : undefined });
      } else {
        await put('sync_queue', row.queueId, { ...row, state: 'synced', syncedAt: Date.now() });
      }
    }
    return { ok: true, count: result.processed || 0 };
  } catch {
    return { ok: false, count: await pendingCount() };
  }
}
