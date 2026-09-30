import { openDB, type IDBPDatabase } from 'idb';

const DB = 'bhashasaathi-local';
const VERSION = 3;
const STORES = ['session', 'groups', 'lessons', 'audio', 'attempts', 'sync_queue', 'app_metadata', 'live_history'] as const;

type StoreName = (typeof STORES)[number];

async function db(): Promise<IDBPDatabase> {
  return openDB(DB, VERSION, {
    upgrade(database) {
      STORES.forEach((store) => {
        if (!database.objectStoreNames.contains(store)) database.createObjectStore(store, { keyPath: 'key' });
      });
    },
  });
}

export async function put(store: StoreName, key: string, value: unknown) {
  const database = await db();
  await database.put(store, { key, value, updatedAt: Date.now() });
}

export async function get<T>(store: StoreName, key: string): Promise<T | undefined> {
  const database = await db();
  const row = await database.get(store, key);
  return row?.value as T | undefined;
}

export async function all<T>(store: StoreName): Promise<T[]> {
  const database = await db();
  return (await database.getAll(store)).map((row: any) => row.value as T);
}

export async function del(store: StoreName, key: string) {
  const database = await db();
  await database.delete(store, key);
}

export async function clearStore(store: StoreName) {
  const database = await db();
  await database.clear(store);
}

export async function clearOfflineContent() {
  await Promise.all([
    clearStore('groups'),
    clearStore('lessons'),
    clearStore('audio'),
    clearStore('attempts'),
    clearStore('sync_queue'),
    clearStore('app_metadata'),
  ]);
}
