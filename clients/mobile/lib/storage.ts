import AsyncStorage from '@react-native-async-storage/async-storage';

export const HOST_KEY = 'eli.host';
export const TOKEN_KEY = 'eli.token';
export const SESSION_KEY = 'eli.session';

export async function loadConnection(): Promise<{
  host: string;
  token: string;
  sessionId: string | null;
}> {
  const [host, token, sessionId] = await Promise.all([
    AsyncStorage.getItem(HOST_KEY),
    AsyncStorage.getItem(TOKEN_KEY),
    AsyncStorage.getItem(SESSION_KEY),
  ]);
  return {
    host: host || '',
    token: token || '',
    sessionId: sessionId || null,
  };
}

export async function saveConnection(host: string, token: string): Promise<void> {
  await AsyncStorage.multiSet([
    [HOST_KEY, host],
    [TOKEN_KEY, token.trim()],
  ]);
}

export async function saveSession(sessionId: string | null): Promise<void> {
  if (sessionId) await AsyncStorage.setItem(SESSION_KEY, sessionId);
  else await AsyncStorage.removeItem(SESSION_KEY);
}
