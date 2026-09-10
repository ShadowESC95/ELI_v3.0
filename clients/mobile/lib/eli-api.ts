/** Thin client for ELI's local FastAPI server (api/server.py). */

export const DEFAULT_PORT = 8081;

/** Accepts "192.168.1.50", "192.168.1.50:8081", or a full http(s) URL. */
export function normaliseHost(raw: string): string {
  let h = (raw || '').trim().replace(/\/+$/, '');
  if (!h) return '';
  if (!/^https?:\/\//i.test(h)) h = `http://${h}`;
  const hostPart = h.replace(/^https?:\/\//i, '').split('/')[0];
  if (!/:\d+$/.test(hostPart)) h = `${h}:${DEFAULT_PORT}`;
  return h;
}

export type StreamHandlers = {
  onDelta: (accumulated: string) => void;
  onSession?: (sessionId: string) => void;
};

function authHeaders(token: string, accept: string): Record<string, string> {
  const h: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: accept,
  };
  if (token.trim()) h.Authorization = `Bearer ${token.trim()}`;
  return h;
}

const SSE_SEP = '\n\n';

function parseSseChunk(
  frame: string,
  state: { raw: string; sessionId: string | null },
  handlers: StreamHandlers,
): void {
  if (!frame.startsWith('data:')) return;
  const payload = frame.slice(5).trim();
  if (!payload || payload === '[DONE]') return;
  try {
    const j = JSON.parse(payload) as {
      session_id?: string;
      delta?: string;
      error?: string;
      done?: boolean;
    };
    if (j.session_id) {
      state.sessionId = j.session_id;
      handlers.onSession?.(j.session_id);
    }
    if (j.delta) {
      state.raw += j.delta;
      handlers.onDelta(state.raw);
    }
    if (j.error) {
      state.raw += (state.raw ? '\n' : '') + `[error: ${j.error}]`;
      handlers.onDelta(state.raw);
    }
  } catch {
    // ignore malformed frames
  }
}

/** Stream a reply via POST /v1/chat/stream (SSE). Falls back to /v1/chat. */
export async function chatStream(
  base: string,
  token: string,
  message: string,
  sessionId: string | null,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<{ text: string; sessionId: string | null }> {
  const body = JSON.stringify({
    message,
    ...(sessionId ? { session_id: sessionId } : {}),
  });
  const headers = authHeaders(token, 'text/event-stream');

  const res = await fetch(`${base}/v1/chat/stream`, {
    method: 'POST',
    headers,
    body,
    signal,
  });

  if (res.status === 401 || res.status === 403) {
    throw new Error(
      'Unauthorized — ELI requires a bearer token when bound beyond loopback. ' +
        'Start the server with --lan and paste the token from Settings.',
    );
  }
  if (!res.ok) throw new Error(`Server returned ${res.status}`);

  const state = { raw: '', sessionId: sessionId as string | null };

  const bodyStream = res.body as { getReader?: () => ReadableStreamDefaultReader<Uint8Array> } | null;
  if (bodyStream?.getReader) {
    const reader = bodyStream.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i: number;
      while ((i = buf.indexOf(SSE_SEP)) >= 0) {
        const frame = buf.slice(0, i);
        buf = buf.slice(i + SSE_SEP.length);
        parseSseChunk(frame, state, handlers);
      }
    }
    if (buf.trim()) parseSseChunk(buf.trim(), state, handlers);
  } else {
    const text = await res.text();
    for (const frame of text.split(SSE_SEP)) {
      parseSseChunk(frame.trim(), state, handlers);
    }
  }

  if (!state.raw) {
    return chatOnce(base, token, message, sessionId, signal);
  }
  return { text: state.raw, sessionId: state.sessionId };
}

/** Non-streaming fallback — POST /v1/chat. */
export async function chatOnce(
  base: string,
  token: string,
  message: string,
  sessionId: string | null,
  signal?: AbortSignal,
): Promise<{ text: string; sessionId: string | null }> {
  const res = await fetch(`${base}/v1/chat`, {
    method: 'POST',
    headers: authHeaders(token, 'application/json'),
    body: JSON.stringify({
      message,
      ...(sessionId ? { session_id: sessionId } : {}),
    }),
    signal,
  });
  if (res.status === 401 || res.status === 403) {
    throw new Error('Unauthorized — check your API token.');
  }
  if (!res.ok) throw new Error(`Server returned ${res.status}`);
  const data = (await res.json()) as { response?: string; session_id?: string };
  return {
    text: String(data.response ?? '').trim() || '(empty reply)',
    sessionId: data.session_id ?? sessionId,
  };
}

export async function checkHealth(
  base: string,
  token: string,
  signal?: AbortSignal,
): Promise<boolean> {
  try {
    const h = authHeaders(token, 'application/json');
    delete h['Content-Type'];
    const r = await fetch(`${base}/health`, { headers: h, signal });
    return r.ok;
  } catch {
    return false;
  }
}

/** Parse QR / connect URLs from ELI's Connect tab or manual entry. */
export function parseConnectUrl(url: string): { host: string; token: string } {
  const out = { host: '', token: '' };
  const raw = (url || '').trim();
  if (!raw) return out;

  try {
    const u = raw.includes('://') ? new URL(raw) : new URL(`http://${raw}`);
    out.host = normaliseHost(`${u.protocol}//${u.host}`);
    out.token = u.searchParams.get('token') || '';
    if (!out.token && u.hash.startsWith('#token=')) {
      out.token = u.hash.slice('#token='.length);
    }
  } catch {
    out.host = normaliseHost(raw);
  }
  return out;
}
