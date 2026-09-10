/**
 * ELI Mobile Companion — optional native shell for the ELI home server (v3).
 *
 * Inference stays on your PC; this app is a thin client (same role as the PWA).
 * Distribute via PWA, GitHub APK sideload, or build-from-source — stores optional.
 */
import * as Haptics from 'expo-haptics';
import * as Linking from 'expo-linking';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  FlatList,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import {
  chatStream,
  checkHealth,
  normaliseHost,
  parseConnectUrl,
} from '../lib/eli-api';
import { loadConnection, saveConnection, saveSession } from '../lib/storage';

type Msg = { id: string; role: 'user' | 'eli' | 'error'; text: string };

export default function CompanionScreen() {
  const [host, setHost] = useState('');
  const [token, setToken] = useState('');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [messages, setMessages] = useState<Msg[]>([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<'unknown' | 'ok' | 'down'>('unknown');
  const [showSettings, setShowSettings] = useState(false);
  const listRef = useRef<FlatList<Msg>>(null);
  const abortRef = useRef<AbortController | null>(null);
  const streamMsgId = useRef<string | null>(null);

  const applyConnectUrl = useCallback((url: string) => {
    const parsed = parseConnectUrl(url);
    if (parsed.host) setHost(parsed.host);
    if (parsed.token) setToken(parsed.token);
    if (parsed.host) setShowSettings(true);
  }, []);

  useEffect(() => {
    (async () => {
      const saved = await loadConnection();
      if (saved.host) setHost(saved.host);
      if (saved.token) setToken(saved.token);
      if (saved.sessionId) setSessionId(saved.sessionId);
      setShowSettings(!saved.host);
    })();

    Linking.getInitialURL().then((url) => {
      if (url) applyConnectUrl(url);
    });
    const sub = Linking.addEventListener('url', ({ url }) => applyConnectUrl(url));
    return () => sub.remove();
  }, [applyConnectUrl]);

  const base = normaliseHost(host);

  const refreshHealth = useCallback(async () => {
    if (!base) return;
    const ok = await checkHealth(base, token);
    setStatus(ok ? 'ok' : 'down');
  }, [base, token]);

  useEffect(() => {
    refreshHealth();
  }, [refreshHealth]);

  const push = (m: Msg) => {
    setMessages((prev) => [...prev, m]);
    requestAnimationFrame(() => listRef.current?.scrollToEnd({ animated: true }));
  };

  const updateStream = (text: string) => {
    const id = streamMsgId.current;
    if (!id) return;
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, text, role: 'eli' as const } : m)),
    );
    requestAnimationFrame(() => listRef.current?.scrollToEnd({ animated: true }));
  };

  const stopStream = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
  };

  const saveSettings = async () => {
    const normalized = normaliseHost(host);
    setHost(normalized);
    await saveConnection(normalized, token);
    setShowSettings(false);
    refreshHealth();
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
  };

  const newChat = async () => {
    setSessionId(null);
    setMessages([]);
    streamMsgId.current = null;
    await saveSession(null);
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || busy) return;
    if (!base) {
      setShowSettings(true);
      return;
    }

    push({ id: `u${Date.now()}`, role: 'user', text });
    setDraft('');
    setBusy(true);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);

    const eliId = `e${Date.now()}`;
    streamMsgId.current = eliId;
    push({ id: eliId, role: 'eli', text: '…' });

    const ctl = new AbortController();
    abortRef.current = ctl;

    try {
      const result = await chatStream(
        base,
        token,
        text,
        sessionId,
        {
          onDelta: updateStream,
          onSession: (sid) => {
            setSessionId(sid);
            saveSession(sid);
          },
        },
        ctl.signal,
      );
      if (result.sessionId && result.sessionId !== sessionId) {
        setSessionId(result.sessionId);
        await saveSession(result.sessionId);
      }
      updateStream(result.text);
      setStatus('ok');
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } catch (err: unknown) {
      if ((err as Error)?.name === 'AbortError') {
        updateStream('(stopped)');
      } else {
        setStatus('down');
        const msg = err instanceof Error ? err.message : String(err);
        streamMsgId.current = null;
        setMessages((prev) => prev.filter((m) => m.id !== eliId));
        push({
          id: `x${Date.now()}`,
          role: 'error',
          text: `${msg}\n\nTried ${base}/v1/chat/stream — is ELI running with --lan on the same Wi‑Fi?`,
        });
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      }
    } finally {
      abortRef.current = null;
      streamMsgId.current = null;
      setBusy(false);
    }
  };

  return (
    <SafeAreaView style={s.safe} edges={['top']}>
      <View style={s.bar}>
        <View style={s.brandRow}>
          <View
            style={[
              s.dot,
              status === 'ok' ? s.dotOk : status === 'down' ? s.dotBad : s.dotIdle,
            ]}
          />
          <Text style={s.brand}>ELI</Text>
          <Text style={s.subbrand}>Companion</Text>
        </View>
        <View style={s.barActions}>
          <Pressable onPress={newChat} hitSlop={8}>
            <Text style={s.action}>New</Text>
          </Pressable>
          <Pressable onPress={() => setShowSettings((v) => !v)} hitSlop={8}>
            <Text style={s.action}>Settings</Text>
          </Pressable>
        </View>
      </View>

      {showSettings && (
        <View style={s.settings}>
          <Text style={s.label}>Server address</Text>
          <TextInput
            style={s.input}
            value={host}
            onChangeText={setHost}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="url"
            placeholder="192.168.1.100:8081"
            placeholderTextColor="#5b6b86"
          />
          <Text style={s.label}>API token</Text>
          <TextInput
            style={s.input}
            value={token}
            onChangeText={setToken}
            autoCapitalize="none"
            autoCorrect={false}
            secureTextEntry
            placeholder="from ELI --lan or Connect QR URL"
            placeholderTextColor="#5b6b86"
          />
          <Text style={s.hint}>
            Run ELI on your PC: ELI --server --lan (or ./scripts/eli_serve.sh --lan).
            Phone and PC must share Wi‑Fi. Paste http://IP:8081/?token=… from the Connect tab,
            or use the PWA in your browser with no install — see docs/SERVER_AND_WEB_APP.md.
          </Text>
          <Pressable style={s.save} onPress={saveSettings}>
            <Text style={s.saveText}>Save connection</Text>
          </Pressable>
        </View>
      )}

      <FlatList
        ref={listRef}
        data={messages}
        keyExtractor={(m) => m.id}
        contentContainerStyle={s.list}
        renderItem={({ item }) => (
          <View
            style={[
              s.bubble,
              item.role === 'user' ? s.user : item.role === 'error' ? s.error : s.eli,
            ]}>
            <Text style={item.role === 'user' ? s.userText : s.eliText}>{item.text}</Text>
          </View>
        )}
        ListEmptyComponent={
          <Text style={s.empty}>
            Your phone talks to ELI on your own computer.{'\n'}
            Nothing is sent to the cloud.
          </Text>
        }
      />

      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <View style={s.composer}>
          <TextInput
            style={s.composerInput}
            value={draft}
            onChangeText={setDraft}
            placeholder="Message ELI…"
            placeholderTextColor="#5b6b86"
            multiline
            editable={!busy}
          />
          <Pressable
            style={[s.send, busy && s.sendBusy]}
            onPress={busy ? stopStream : send}
            disabled={!busy && !draft.trim()}>
            {busy ? (
              <Text style={s.sendText}>Stop</Text>
            ) : (
              <Text style={s.sendText}>Send</Text>
            )}
          </Pressable>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#05070d' },
  bar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(64,224,255,0.15)',
  },
  brandRow: { flexDirection: 'row', alignItems: 'baseline', gap: 8 },
  dot: { width: 8, height: 8, borderRadius: 4, alignSelf: 'center' },
  dotOk: { backgroundColor: '#22d3ee' },
  dotBad: { backgroundColor: '#ff5d73' },
  dotIdle: { backgroundColor: '#5b6b86' },
  brand: { color: '#22d3ee', fontSize: 17, fontWeight: '800', letterSpacing: 3 },
  subbrand: { color: '#5b6b86', fontSize: 12, fontWeight: '600' },
  barActions: { flexDirection: 'row', gap: 18 },
  action: { color: '#8aa4c8', fontSize: 14 },
  settings: {
    padding: 16,
    gap: 8,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(64,224,255,0.15)',
    backgroundColor: 'rgba(13,20,33,0.62)',
  },
  label: {
    color: '#22d3ee',
    fontSize: 11,
    letterSpacing: 1.5,
    textTransform: 'uppercase',
    marginTop: 4,
  },
  input: {
    borderWidth: 1,
    borderColor: 'rgba(64,224,255,0.15)',
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    color: '#dbeafe',
    backgroundColor: 'rgba(7,12,22,0.72)',
  },
  hint: { color: '#5b6b86', fontSize: 12, marginTop: 6, lineHeight: 17 },
  save: {
    marginTop: 10,
    backgroundColor: '#22d3ee',
    borderRadius: 10,
    paddingVertical: 11,
    alignItems: 'center',
  },
  saveText: { color: '#03121a', fontWeight: '700' },
  list: { padding: 14, gap: 10, flexGrow: 1 },
  empty: { color: '#5b6b86', textAlign: 'center', marginTop: 48, fontSize: 14, lineHeight: 22 },
  bubble: { maxWidth: '88%', borderRadius: 14, paddingHorizontal: 14, paddingVertical: 10 },
  user: { alignSelf: 'flex-end', backgroundColor: '#22d3ee' },
  eli: {
    alignSelf: 'flex-start',
    backgroundColor: 'rgba(20,28,44,0.55)',
    borderWidth: 1,
    borderColor: 'rgba(64,224,255,0.15)',
  },
  error: {
    alignSelf: 'flex-start',
    backgroundColor: 'rgba(255,93,115,0.10)',
    borderWidth: 1,
    borderColor: 'rgba(255,93,115,0.35)',
  },
  userText: { color: '#03121a', fontSize: 15, lineHeight: 21 },
  eliText: { color: '#dbeafe', fontSize: 15, lineHeight: 21 },
  composer: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: 10,
    padding: 12,
    borderTopWidth: 1,
    borderTopColor: 'rgba(64,224,255,0.15)',
  },
  composerInput: {
    flex: 1,
    maxHeight: 120,
    borderWidth: 1,
    borderColor: 'rgba(64,224,255,0.15)',
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 10,
    color: '#dbeafe',
    backgroundColor: 'rgba(7,12,22,0.72)',
  },
  send: {
    backgroundColor: '#22d3ee',
    borderRadius: 12,
    paddingHorizontal: 18,
    paddingVertical: 12,
    minWidth: 72,
    alignItems: 'center',
  },
  sendBusy: { backgroundColor: '#ff5d73' },
  sendText: { color: '#03121a', fontWeight: '700' },
});
