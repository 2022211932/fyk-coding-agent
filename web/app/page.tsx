'use client';

import { FormEvent, useCallback, useEffect, useRef, useState, type ReactNode } from 'react';

type AgentEvent = {
  type: string;
  message?: string;
  step?: number;
  tool?: string;
  arguments?: Record<string, unknown>;
  result?: Record<string, unknown>;
  text?: string;
  error?: string;
  steps?: number;
  stop_reason?: string;
  compactions?: number;
  approval_id?: string;
  decision?: 'allow' | 'allow_all' | 'reject';
  force_manual?: boolean;
  risk_reason?: string;
  timestamp?: string;
  context_chars?: number;
  message_count?: number;
  context_compactions?: number;
  max_context_chars?: number;
  plan?: TaskPlan;
  unfinished?: string[];
};

type PlanEvidence = { id: string; tool: string; ok: boolean; summary: string; step: number; verification: boolean; error_type?: string };
type PlanStep = { id: string; title: string; kind: 'inspect' | 'change' | 'verify' | 'other'; status: 'pending' | 'in_progress' | 'completed' | 'blocked'; evidence_ids: string[]; note: string; blocker_type?: 'tool_failure' | 'missing_prerequisite' | 'environment' | 'user_input_required' };
type TaskPlan = { summary: string; steps: PlanStep[]; completed: number; total: number; terminal: boolean; blocked: boolean; evidence: PlanEvidence[] };

type Status = {
  version: string;
  model: string;
  workspace: string;
  automatic_approval: boolean;
  max_context_chars: number;
};

type FileEntry = { path: string; type: string; size?: number };
type DirectoryEntry = { name: string; path: string; type: 'directory' | 'root' };
type DirectoryListing = {
  current: string | null;
  parent: string | null;
  entries: DirectoryEntry[];
};
type Projects = { current: string; recent: string[]; roots: DirectoryEntry[] };
type ConversationSession = {
  id: string;
  title: string;
  events: AgentEvent[];
  updatedAt: number;
  pinned: boolean;
  archived: boolean;
  contextChars: number;
  messageCount: number;
  contextCompactions: number;
};
type ConversationStore = { items: ConversationSession[]; activeId: string };
type DiffView = { path: string; diff: string; truncated: boolean };

const demoSessions = [
  { title: '修复 slugify 测试', time: '刚刚', active: true },
  { title: '实现 01 背包算法', time: '12 分钟前', active: false },
  { title: '检查项目安全边界', time: '昨天', active: false },
];

export default function Home() {
  const [prompt, setPrompt] = useState('');
  const [conversationStore, setConversationStore] = useState<ConversationStore>(() => {
    const session = createConversationSession();
    return { items: [session], activeId: session.id };
  });
  const [status, setStatus] = useState<Status | null>(null);
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [running, setRunning] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [connectionError, setConnectionError] = useState('');
  const [approval, setApproval] = useState<AgentEvent | null>(null);
  const [projectPickerOpen, setProjectPickerOpen] = useState(false);
  const [projects, setProjects] = useState<Projects | null>(null);
  const [directory, setDirectory] = useState<DirectoryListing | null>(null);
  const [pickerError, setPickerError] = useState('');
  const [switchingWorkspace, setSwitchingWorkspace] = useState(false);
  const [openSessionMenu, setOpenSessionMenu] = useState<string | null>(null);
  const [deleteConfirmation, setDeleteConfirmation] = useState<string | null>(null);
  const [sessionActionError, setSessionActionError] = useState<{ id: string; message: string } | null>(null);
  const [diffView, setDiffView] = useState<DiffView | null>(null);
  const [diffLoading, setDiffLoading] = useState('');
  const connection = useRef({ api: '', token: '' });
  const timelineEnd = useRef<HTMLDivElement>(null);

  const live = Boolean(status);
  const activeSession = conversationStore.items.find((session) => session.id === conversationStore.activeId) || conversationStore.items[0];
  const events = activeSession.events;
  const sessionId = activeSession.id;
  const toolCalls = events.filter((event) => event.type === 'tool_call').length;
  const latestStep = events.reduce((max, event) => Math.max(max, event.step || event.steps || 0), 0);
  const fileChanges = events.filter((event) => event.type === 'tool_result' && ['edit_file', 'write_file'].includes(event.tool || '') && event.result?.ok);
  const currentTitle = live ? activeSession.title : '修复 slugify 测试';
  const orderedSessions = [...conversationStore.items].sort((left, right) => Number(right.pinned) - Number(left.pinned) || right.updatedAt - left.updatedAt);
  const recentSessions = orderedSessions.filter((session) => !session.archived);
  const archivedSessions = orderedSessions.filter((session) => session.archived);
  const contextPercent = status ? Math.min(100, (activeSession.contextChars / status.max_context_chars) * 100) : 24;
  const latestPlanEvent = [...events].reverse().find((event) => ['plan_updated', 'plan_reset'].includes(event.type));
  const activePlan = latestPlanEvent?.type === 'plan_updated' ? latestPlanEvent.plan : undefined;

  function updateSession(targetId: string, update: (session: ConversationSession) => ConversationSession) {
    setConversationStore((previous) => ({
      ...previous,
      items: previous.items.map((session) => session.id === targetId ? update(session) : session),
    }));
  }

  function appendEvent(targetId: string, event: AgentEvent) {
    updateSession(targetId, (session) => ({
      ...session,
      events: [...session.events, event],
      updatedAt: Date.now(),
      contextChars: event.context_chars ?? session.contextChars,
      messageCount: event.message_count ?? session.messageCount,
      contextCompactions: event.context_compactions ?? session.contextCompactions,
    }));
  }

  const persistSession = useCallback(async (session: ConversationSession, fields?: Partial<Pick<ConversationSession, 'title' | 'pinned' | 'archived'>>) => {
    const { api, token } = connection.current;
    if (!api) return;
    const payload = { session_id: session.id, ...(fields || {}) };
    const response = await fetch(`${api}/api/sessions/update`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Yukai-Token': token },
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error('无法保存会话状态');
  }, []);

  const loadSessions = useCallback(async (api: string, token: string) => {
    const response = await fetch(`${api}/api/sessions`, { headers: { 'X-Yukai-Token': token } });
    if (!response.ok) throw new Error('无法恢复历史会话');
    const data = await response.json();
    const restored: ConversationSession[] = (data.sessions || []).map((item: Record<string, unknown>) => ({
      id: String(item.id),
      title: String(item.title || '新会话'),
      events: Array.isArray(item.events) ? item.events as AgentEvent[] : [],
      updatedAt: Number(item.updated_at || Date.now()),
      pinned: Boolean(item.pinned),
      archived: Boolean(item.archived),
      contextChars: Number(item.context_chars || 0),
      messageCount: Number(item.message_count || 0),
      contextCompactions: Number(item.context_compactions || 0),
    }));
    if (restored.length) {
      const active = restored.find((item) => !item.archived) || restored[0];
      setConversationStore({ items: restored, activeId: active.id });
      return;
    }
    const fresh = createConversationSession();
    setConversationStore({ items: [fresh], activeId: fresh.id });
    await persistSession(fresh);
  }, [persistSession]);

  async function startNewSession() {
    if (!live || running) return;
    const session = createConversationSession();
    setConversationStore((previous) => ({ items: [session, ...previous.items], activeId: session.id }));
    setOpenSessionMenu(null);
    setDeleteConfirmation(null);
    setSessionActionError(null);
    setApproval(null);
    setPrompt('');
    try {
      await persistSession(session);
    } catch (error) {
      setConnectionError(error instanceof Error ? error.message : '无法保存新会话');
    }
  }

  function switchSession(targetId: string) {
    if (running || targetId === sessionId) return;
    setConversationStore((previous) => ({ ...previous, activeId: targetId }));
    setOpenSessionMenu(null);
    setDeleteConfirmation(null);
    setSessionActionError(null);
    setApproval(null);
    setPrompt('');
  }

  function toggleSessionMenu(targetId: string) {
    setOpenSessionMenu((current) => current === targetId ? null : targetId);
    setDeleteConfirmation(null);
    setSessionActionError(null);
  }

  async function togglePinned(targetId: string) {
    const target = conversationStore.items.find((session) => session.id === targetId);
    if (!target) return;
    const pinned = !target.pinned;
    updateSession(targetId, (session) => ({ ...session, pinned }));
    setOpenSessionMenu(null);
    setSessionActionError(null);
    try { await persistSession(target, { pinned }); } catch (error) { setSessionActionError({ id: targetId, message: error instanceof Error ? error.message : '无法保存置顶状态' }); }
  }

  async function toggleArchived(targetId: string) {
    const selected = conversationStore.items.find((session) => session.id === targetId);
    if (!selected) return;
    const archived = !selected.archived;
    let replacement: ConversationSession | null = null;
    let items = conversationStore.items.map((session) => session.id === targetId ? { ...session, archived } : session);
    let activeId = conversationStore.activeId;
    if (archived && activeId === targetId) {
        const next = items.filter((session) => !session.archived).sort((left, right) => Number(right.pinned) - Number(left.pinned) || right.updatedAt - left.updatedAt)[0];
        if (next) activeId = next.id;
        else {
          replacement = createConversationSession();
          items = [replacement, ...items];
          activeId = replacement.id;
        }
    }
    setConversationStore({ items, activeId });
    setOpenSessionMenu(null);
    setDeleteConfirmation(null);
    setSessionActionError(null);
    setApproval(null);
    setPrompt('');
    try {
      await persistSession(selected, { archived });
      if (replacement) await persistSession(replacement);
    } catch (error) {
      setSessionActionError({ id: targetId, message: error instanceof Error ? error.message : '无法保存归档状态' });
    }
  }

  async function deleteSession(targetId: string) {
    if (deleteConfirmation !== targetId) {
      setDeleteConfirmation(targetId);
      setSessionActionError(null);
      return;
    }
    const { api, token } = connection.current;
    try {
      let response = await fetch(`${api}/api/sessions/delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Yukai-Token': token },
        body: JSON.stringify({ session_id: targetId }),
      });
      if (response.status === 404) {
        response = await fetch(`${api}/api/clear`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Yukai-Token': token },
          body: JSON.stringify({ session_id: targetId }),
        });
      }
      if (!response.ok) throw new Error('无法删除该会话');
      let items = conversationStore.items.filter((session) => session.id !== targetId);
      let activeId = conversationStore.activeId;
      let replacement: ConversationSession | null = null;
        if (activeId === targetId) {
          const next = items.filter((session) => !session.archived).sort((left, right) => Number(right.pinned) - Number(left.pinned) || right.updatedAt - left.updatedAt)[0];
          if (next) activeId = next.id;
          else {
            replacement = createConversationSession();
            items = [replacement, ...items];
            activeId = replacement.id;
          }
        }
      setConversationStore({ items, activeId });
      if (replacement) await persistSession(replacement);
      setOpenSessionMenu(null);
      setDeleteConfirmation(null);
      setSessionActionError(null);
      setConnectionError('');
      setApproval(null);
      setPrompt('');
    } catch (error) {
      setDeleteConfirmation(null);
      setSessionActionError({ id: targetId, message: error instanceof Error ? error.message : '无法删除该会话' });
    }
  }

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const api = params.get('api') || window.sessionStorage.getItem('yukai-api') || window.sessionStorage.getItem('fyk-api');
    const token = params.get('token') || window.sessionStorage.getItem('yukai-token') || window.sessionStorage.getItem('fyk-token');
    if (!api || !token) return;
    window.sessionStorage.setItem('yukai-api', api);
    window.sessionStorage.setItem('yukai-token', token);
    window.history.replaceState({}, '', window.location.pathname);
    connection.current = { api, token };
    const connect = async () => {
      try {
        const headers = { 'X-Yukai-Token': token };
        const response = await fetch(`${api}/api/status`, { headers });
        if (!response.ok) throw new Error(`连接失败 (${response.status})`);
        setStatus(await response.json());
        await loadSessions(api, token);
        const fileResponse = await fetch(`${api}/api/files?path=.`, { headers });
        const fileData = await fileResponse.json();
        if (fileData.ok) setFiles(fileData.entries || []);
      } catch (error) {
        setConnectionError(error instanceof Error ? error.message : '无法连接本地 Agent');
      }
    };
    void connect();
  }, [loadSessions]);

  useEffect(() => {
    if (!openSessionMenu) return;
    const closeMenu = (event: MouseEvent) => {
      if (event.target instanceof Element && event.target.closest('[data-session-menu]')) return;
      setOpenSessionMenu(null);
      setDeleteConfirmation(null);
      setSessionActionError(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpenSessionMenu(null);
        setDeleteConfirmation(null);
        setSessionActionError(null);
      }
    };
    document.addEventListener('mousedown', closeMenu);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('mousedown', closeMenu);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [openSessionMenu]);

  useEffect(() => {
    timelineEnd.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [events]);

  async function refreshFiles() {
    const { api, token } = connection.current;
    if (!api) return;
    const response = await fetch(`${api}/api/files?path=.`, { headers: { 'X-Yukai-Token': token } });
    const data = await response.json();
    if (data.ok) setFiles(data.entries || []);
  }

  async function toggleAutomaticApproval() {
    if (!status || running) return;
    const { api, token } = connection.current;
    try {
      const response = await fetch(`${api}/api/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Yukai-Token': token },
        body: JSON.stringify({ automatic_approval: !status.automatic_approval }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '无法切换自动审批');
      setStatus(data);
    } catch (error) {
      setConnectionError(error instanceof Error ? error.message : '无法切换自动审批');
    }
  }

  async function browseDirectory(path?: string) {
    const { api, token } = connection.current;
    const query = path ? `?path=${encodeURIComponent(path)}` : '';
    setPickerError('');
    try {
      const response = await fetch(`${api}/api/directories${query}`, {
        headers: { 'X-Yukai-Token': token },
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '无法读取目录');
      setDirectory(data);
    } catch (error) {
      setPickerError(error instanceof Error ? error.message : '无法读取目录');
    }
  }

  async function openProjectPicker() {
    if (!status || running) return;
    setProjectPickerOpen(true);
    setPickerError('');
    const { api, token } = connection.current;
    try {
      const response = await fetch(`${api}/api/projects`, {
        headers: { 'X-Yukai-Token': token },
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '无法加载本地项目');
      setProjects(data);
      await browseDirectory(status.workspace);
    } catch (error) {
      setPickerError(error instanceof Error ? error.message : '无法加载本地项目');
    }
  }

  async function selectWorkspace(path: string) {
    if (running || switchingWorkspace) return;
    const { api, token } = connection.current;
    setSwitchingWorkspace(true);
    setPickerError('');
    try {
      const response = await fetch(`${api}/api/workspace`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Yukai-Token': token },
        body: JSON.stringify({ path }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '无法切换工作区');
      setStatus(data);
      await loadSessions(api, token);
      setApproval(null);
      setProjectPickerOpen(false);
      await refreshFiles();
    } catch (error) {
      setPickerError(error instanceof Error ? error.message : '无法切换工作区');
    } finally {
      setSwitchingWorkspace(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const message = prompt.trim();
    if (!message || running) return;
    if (!live) {
      setPrompt('');
      setConnectionError('请使用 yukai --web 启动实时控制台。当前页面为交互演示。');
      return;
    }
    setPrompt('');
    setRunning(true);
    setConnectionError('');
    const runSessionId = sessionId;
    updateSession(runSessionId, (session) => ({
      ...session,
      title: session.events.some((item) => item.type === 'user') ? session.title : compact(message, 34),
      events: [...session.events, { type: 'user', message, timestamp: now() }],
      updatedAt: Date.now(),
    }));
    const { api, token } = connection.current;
    try {
      const response = await fetch(`${api}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Yukai-Token': token },
        body: JSON.stringify({ message, session_id: runSessionId }),
      });
      if (!response.ok || !response.body) {
        const body = await response.text();
        throw new Error(body || `请求失败 (${response.status})`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (!line.trim()) continue;
          const item = JSON.parse(line) as AgentEvent;
          item.timestamp = now();
          if (item.type === 'approval_required') setApproval(item);
          appendEvent(runSessionId, item);
        }
        if (done) break;
      }
      await refreshFiles();
    } catch (error) {
      const messageText = error instanceof Error ? error.message : 'Agent 请求失败';
      appendEvent(runSessionId, { type: 'error', error: messageText, timestamp: now() });
    } finally {
      setRunning(false);
      setStopping(false);
      setApproval(null);
    }
  }

  async function stopTask() {
    if (!running || stopping) return;
    setStopping(true);
    const { api, token } = connection.current;
    try {
      const response = await fetch(`${api}/api/sessions/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Yukai-Token': token },
        body: JSON.stringify({ session_id: sessionId }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '无法停止任务');
      setApproval(null);
    } catch (error) {
      setStopping(false);
      setConnectionError(error instanceof Error ? error.message : '无法停止任务');
    }
  }

  async function decide(decision: 'allow' | 'allow_all' | 'reject') {
    if (!approval?.approval_id) return;
    const forceManual = Boolean(approval.force_manual);
    const { api, token } = connection.current;
    await fetch(`${api}/api/approvals/${approval.approval_id}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Yukai-Token': token },
      body: JSON.stringify({ decision }),
    });
    appendEvent(sessionId, { type: 'approval_decision', approval_id: approval.approval_id, decision, message: ({ allow: '已允许一次', allow_all: '已开启自动审批', reject: '已拒绝' })[decision], timestamp: now() });
    setApproval(null);
    if (decision === 'allow_all' && status && !forceManual) setStatus({ ...status, automatic_approval: true });
  }

  async function undo() {
    if (!live || running) return;
    const { api, token } = connection.current;
    const response = await fetch(`${api}/api/undo`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Yukai-Token': token },
      body: JSON.stringify({ session_id: sessionId }),
    });
    const result = await response.json();
    appendEvent(sessionId, {
      type: result.ok ? 'notice' : 'error',
      message: result.ok ? `已恢复 ${result.path}` : undefined,
      error: result.ok ? undefined : result.error,
      timestamp: now(),
    });
    await refreshFiles();
  }

  async function openDiff(event: AgentEvent) {
    const result = event.result || {};
    const path = String(result.path || '');
    const snapshotId = String(result.snapshot_id || '');
    if (!path || !snapshotId) return;
    const { api, token } = connection.current;
    setDiffLoading(snapshotId);
    setConnectionError('');
    try {
      const response = await fetch(`${api}/api/diff?snapshot_id=${encodeURIComponent(snapshotId)}&path=${encodeURIComponent(path)}`, {
        headers: { 'X-Yukai-Token': token },
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '无法读取 Diff');
      setDiffView({ path: data.path, diff: data.diff || '', truncated: Boolean(data.truncated) });
    } catch (error) {
      setConnectionError(error instanceof Error ? error.message : '无法读取 Diff');
    } finally {
      setDiffLoading('');
    }
  }

  const shownFiles = live ? files.slice(0, 12) : [
    { path: 'demo-workspace', type: 'directory' },
    { path: 'README.md', type: 'file' },
    { path: 'slugify.py', type: 'file' },
    { path: 'test_slugify.py', type: 'file' },
  ];

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand"><span className="brand-mark" aria-hidden="true"><i /></span><span>Yukai</span><span className="version">v{status?.version || '0.2'}</span></div>
        <button className="workspace-pill" type="button" onClick={openProjectPicker} disabled={!live || running} title="选择本地主机上的项目"><span className={`status-dot ${connectionError ? 'offline' : ''}`} /><span className="workspace-path">{live ? compact(status.workspace, 52) : 'Demo · demo-workspace'}</span><span className="workspace-chevron">⌄</span></button>
        <div className="top-actions"><button className="icon-button" aria-label="撤销最近修改" onClick={undo}>↶</button><div className="model-chip"><span>◆</span> {status?.model || 'DeepSeek V4 Pro'}</div></div>
      </header>

      <div className="workbench">
        <aside className="session-rail">
          <button className="new-session" type="button" onClick={startNewSession} disabled={!live || running}><span>＋</span> 新建会话</button>
          <p className="rail-label">最近任务</p>
          <nav aria-label="最近任务">
            {live ? recentSessions.map((session) => <SessionListItem key={session.id} session={session} active={session.id === sessionId} running={running} time={session.id === sessionId && running ? '运行中' : sessionTime(session.updatedAt)} menuOpen={openSessionMenu === session.id} confirmingDelete={deleteConfirmation === session.id} menuError={sessionActionError?.id === session.id ? sessionActionError.message : ''} onSelect={switchSession} onToggleMenu={toggleSessionMenu} onTogglePinned={togglePinned} onToggleArchived={toggleArchived} onDelete={deleteSession} />) : demoSessions.map((session) => (
              <button key={session.title} type="button" className={`session-item ${session.active ? 'active' : ''}`}>
                <span className="session-glyph">{session.active ? '●' : '○'}</span><span className="session-copy"><b>{session.title}</b><small>{session.time}</small></span>
              </button>
            ))}
          </nav>
          {live && archivedSessions.length > 0 && <><p className="rail-label archived-label">已归档</p><nav aria-label="已归档任务">{archivedSessions.map((session) => <SessionListItem key={session.id} session={session} active={session.id === sessionId} running={running} time={sessionTime(session.updatedAt)} menuOpen={openSessionMenu === session.id} confirmingDelete={deleteConfirmation === session.id} menuError={sessionActionError?.id === session.id ? sessionActionError.message : ''} onSelect={switchSession} onToggleMenu={toggleSessionMenu} onTogglePinned={togglePinned} onToggleArchived={toggleArchived} onDelete={deleteSession} />)}</nav></>}
          <div className="rail-bottom"><div><span className="kbd">↶</span><span>撤销修改</span><span className="shortcut">UNDO</span></div><div><span className="kbd">?</span><span>{live ? '本地安全连接' : '演示模式'}</span></div></div>
        </aside>

        <section className="conversation">
          <div className="conversation-head">
            <div><p className="eyebrow">{live ? 'LIVE SESSION' : 'INTERACTIVE DEMO'}</p><h1>{currentTitle}</h1></div>
            <div className={`session-state ${connectionError ? 'offline' : ''}`}><span /><b>{running ? 'Agent 执行中' : live ? 'Agent 在线' : '界面预览'}</b><small>{running ? '实时事件流' : '等待任务'}</small></div>
          </div>

          <div className="timeline" aria-live="polite">
            {connectionError && <div className="connection-banner">{connectionError}</div>}
            {live ? <LiveTimeline events={events} running={running} /> : <DemoTimeline />}
            {live && events.length === 0 && (
              <div className="empty-state"><span className="brand-mark"><i /></span><p className="eyebrow">READY</p><h2>把编程任务交给 Yukai</h2><p>你将实时看到模型思考、工具调用、命令输出、文件变更与审批请求。</p><div><button onClick={() => setPrompt('阅读项目结构，告诉我应该从哪里开始。')}>了解项目</button><button onClick={() => setPrompt('运行测试，定位失败原因并提出修复方案。')}>检查测试</button></div></div>
            )}
            <div ref={timelineEnd} />
          </div>

          <form className="composer" onSubmit={submit}>
            {running && <div className="queue-toast"><span />Agent 正在执行任务</div>}
            <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder={live ? '继续交给 Yukai 一个任务…' : '输入任务体验交互效果…'} aria-label="输入任务" rows={2} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} />
            <div className="composer-foot"><div><button type="button" className="mini-button">＋</button><span><kbd>Shift</kbd> + <kbd>Enter</kbd> 换行</span></div><div><button type="button" className={`approval-switch ${status?.automatic_approval ? 'enabled' : ''}`} role="switch" aria-checked={Boolean(status?.automatic_approval)} onClick={toggleAutomaticApproval} disabled={!live || running} title="安全操作可自动通过，高风险命令始终需要人工确认"><span><i /></span>{status?.automatic_approval ? '安全操作自动审批' : '手动审批'}</button>{running ? <button className="stop-task" type="button" onClick={stopTask} disabled={stopping}><span>■</span>{stopping ? '正在停止…' : '停止任务'}</button> : <button className="send" type="submit" disabled={!prompt.trim()}>运行任务 <span>↵</span></button>}</div></div>
          </form>
        </section>

        <aside className="inspector">
          {live && activePlan && <PlanPanel plan={activePlan} />}
          <section className="inspector-section context-section">
            <div className="panel-heading"><span>会话上下文</span><b>{live ? `${contextPercent.toFixed(contextPercent < 1 ? 1 : 0)}%` : '24%'}</b></div><div className="meter"><i style={{ width: `${live ? contextPercent : 24}%` }} /></div><div className="meter-label"><span>{live ? `${activeSession.messageCount} 条消息` : '12 条消息'}</span><span>{live ? `${formatChars(activeSession.contextChars)} / ${formatChars(status?.max_context_chars || 0)}` : '192k / 800k chars'}</span></div>
            {live && activeSession.contextCompactions > 0 && <div className="context-note">已自动压缩 {activeSession.contextCompactions} 次上下文</div>}
            <div className="stat-grid"><div><b>{live ? latestStep : 6}</b><span>模型步骤</span></div><div><b>{live ? toolCalls : 7}</b><span>工具调用</span></div><div><b>{events.filter((item) => item.type === 'final').length}</b><span>完成任务</span></div></div>
          </section>
          <section className="inspector-section"><div className="panel-heading"><span>文件变更</span><small>{fileChanges.length || (live ? 0 : 1)} FILE</small></div>{live ? <ChangeSummary events={events} loading={diffLoading} onOpen={openDiff} /> : <><button className="change-card"><span className="python-icon">Py</span><div><b>slugify.py</b><small><em>+12</em> <del>−1</del></small></div><span>›</span></button><button className="diff-button">查看完整 Diff <span>↗</span></button></>}</section>
          <section className="inspector-section file-section"><div className="panel-heading"><span>工作区</span><button onClick={refreshFiles}>↻</button></div><div className="file-tree">{shownFiles.map((file) => <div key={file.path}><span>{file.type === 'directory' ? '▾' : file.path.endsWith('.py') ? 'Py' : '≡'}</span><b>{file.path}</b>{events.some((event) => String(event.arguments?.path || '') === file.path && ['write_file', 'edit_file'].includes(event.tool || '')) && <em>M</em>}</div>)}</div></section>
          <section className="safety-card"><span className="shield">◇</span><div><b>危险命令防护已开启</b><small>{live ? '高风险需确认 · 灾难性操作直接拦截' : '演示模式未连接本地文件'}</small></div></section>
        </aside>
      </div>

      {approval && <ApprovalDialog event={approval} stopping={stopping} onDecision={decide} onStop={stopTask} />}
      {projectPickerOpen && <ProjectPicker projects={projects} directory={directory} error={pickerError} switching={switchingWorkspace} onBrowse={browseDirectory} onSelect={selectWorkspace} onClose={() => setProjectPickerOpen(false)} />}
      {diffView && <DiffDialog view={diffView} onClose={() => setDiffView(null)} />}
    </main>
  );
}

function SessionListItem({ session, active, running, time, menuOpen, confirmingDelete, menuError, onSelect, onToggleMenu, onTogglePinned, onToggleArchived, onDelete }: {
  session: ConversationSession;
  active: boolean;
  running: boolean;
  time: string;
  menuOpen: boolean;
  confirmingDelete: boolean;
  menuError: string;
  onSelect: (id: string) => void;
  onToggleMenu: (id: string) => void;
  onTogglePinned: (id: string) => void;
  onToggleArchived: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  return <div className={`session-row ${active ? 'active' : ''}`} data-session-menu>
    <button type="button" className="session-item" onClick={() => onSelect(session.id)} disabled={running}>
      <span className="session-glyph">{session.pinned ? '◆' : active ? '●' : '○'}</span>
      <span className="session-copy"><b>{session.title}</b><small>{session.pinned ? `已置顶 · ${time}` : time}</small></span>
    </button>
    <button type="button" className="session-more" aria-label={`管理会话：${session.title}`} aria-haspopup="menu" aria-expanded={menuOpen} onClick={() => onToggleMenu(session.id)} disabled={running}>···</button>
    {menuOpen && <div className="session-menu" role="menu">
      <button type="button" role="menuitem" onClick={() => onTogglePinned(session.id)}><span>◆</span>{session.pinned ? '取消置顶' : '置顶'}</button>
      <button type="button" role="menuitem" onClick={() => onToggleArchived(session.id)}><span>▣</span>{session.archived ? '取消归档' : '归档'}</button>
      <button type="button" role="menuitem" className={`delete ${confirmingDelete ? 'confirming' : ''}`} onClick={() => onDelete(session.id)}><span>×</span>{confirmingDelete ? '再次点击确认' : '删除'}</button>
      {menuError && <p className="session-menu-error">{menuError}</p>}
    </div>}
  </div>;
}

function LiveTimeline({ events, running }: { events: AgentEvent[]; running: boolean }) {
  const approvalDecisions = new Map(
    events
      .filter((event) => event.type === 'approval_decision' && event.approval_id)
      .map((event) => [event.approval_id as string, event])
  );
  return <>{events.map((event, index) => {
    if (event.type === 'run_started') return null;
    if (event.type === 'user') return <article className="user-turn" key={index}><div className="avatar user-avatar">FY</div><div className="turn-body"><div className="turn-meta"><b>你</b><time>{event.timestamp}</time></div><p>{event.message}</p></div></article>;
    if (event.type === 'model_request') return <div className="live-activity thinking-row" key={index}><span className="pulse" /><div><b>DeepSeek 正在思考</b><small>模型步骤 {event.step}</small></div></div>;
    if (event.type === 'tool_call') return <div className="live-activity" key={index}><span className="activity-icon">{toolIcon(event.tool)}</span><div><b>{toolLabel(event)}</b><small>{toolDetail(event)}</small></div><span className="running-dot" /></div>;
    if (event.type === 'tool_result') return <ToolResult event={event} key={index} />;
    if (event.type === 'approval_required') {
      const resolved = event.approval_id ? approvalDecisions.get(event.approval_id) : undefined;
      const rejected = resolved?.decision === 'reject';
      const title = resolved ? (rejected ? '操作已拒绝' : resolved.decision === 'allow_all' ? '已允许并开启自动审批' : '已允许本次操作') : event.force_manual ? '高风险操作等待确认' : '等待操作审批';
      return <div className={`live-activity approval-row ${resolved ? rejected ? 'rejected' : 'resolved' : ''}`} key={index}><span className="activity-icon">{resolved ? rejected ? '×' : '✓' : '!'}</span><div><b>{title}</b><small>{event.risk_reason ? riskReasonLabel(event.risk_reason) : toolLabel(event)}</small></div><span className={`exit-badge ${resolved ? rejected ? 'rejected' : 'resolved' : ''}`}>{resolved ? rejected ? 'REJECTED' : 'ALLOWED' : 'REVIEW'}</span></div>;
    }
    if (event.type === 'approval_decision') return event.approval_id ? null : <div className="notice-row" key={index}>审批结果：{event.message}</div>;
    if (event.type === 'plan_incomplete') return <div className="notice-row" key={index}>计划尚未完成：{event.unfinished?.join('、')}</div>;
    if (event.type === 'final') {
      const finalState = finalStatus(event.stop_reason);
      return <article className="agent-turn live-answer" key={index}><div className="avatar agent-avatar"><span>Y</span></div><div className="turn-body"><div className="turn-meta"><b>Yukai</b><span className="thinking-label">{finalState.meta}</span><time>{event.timestamp}</time></div><div className="answer-card"><div className="answer-title"><span>{finalState.icon}</span><b>{finalState.title}</b><small>{event.steps} 个模型步骤</small></div><MarkdownText text={event.text || ''} /></div></div></article>;
    }
    if (event.type === 'notice') return <div className="notice-row success" key={index}>{event.message}</div>;
    if (event.type === 'error') return <div className="connection-banner" key={index}>{event.error}</div>;
    return null;
  })}{running && events.at(-1)?.type !== 'model_request' && <div className="live-activity thinking-row"><span className="pulse" /><div><b>Agent 正在继续</b><small>等待下一个事件</small></div></div>}</>;
}

function ToolResult({ event }: { event: AgentEvent }) {
  const result = event.result || {};
  const ok = Boolean(result.ok);
  const output = String(result.stdout || result.stderr || '');
  const plan = result.plan as TaskPlan | undefined;
  const detail = plan
    ? `${plan.completed}/${plan.total} 个步骤已完成`
    : result.duration_ms
    ? `${Math.round(Number(result.duration_ms))}ms`
    : result.error
      ? String(result.error)
      : result.exit_code !== undefined
        ? `exit ${result.exit_code}`
        : '';
  return <div className={`tool-result-row ${ok ? 'ok' : 'failed'}`}><span>{ok ? '✓' : '×'}</span><div><b>{ok ? '执行成功' : '执行失败'} · {event.tool}</b><small>{detail}</small>{output && <pre>{compact(output, 1600)}</pre>}</div></div>;
}

function PlanPanel({ plan }: { plan: TaskPlan }) {
  const evidence = new Map(plan.evidence.map((item) => [item.id, item]));
  return <section className="inspector-section plan-section">
    <div className="panel-heading"><span>任务计划</span><b>{plan.completed}/{plan.total}</b></div>
    <p className="plan-summary">{plan.summary}</p>
    <div className="plan-list">{plan.steps.map((step) => {
      const proof = step.evidence_ids.map((id) => evidence.get(id)).filter(Boolean) as PlanEvidence[];
      return <div className={`plan-step ${step.status}`} key={step.id}>
        <span className="plan-status">{step.status === 'completed' ? '✓' : step.status === 'in_progress' ? '●' : step.status === 'blocked' ? '!' : '○'}</span>
        <div><b>{step.title}</b><small>{step.status === 'completed' && proof.length ? `证据 · ${proof[0].summary}` : step.status === 'blocked' ? `${blockerTypeLabel(step.blocker_type)} · ${step.note}` : planStatusLabel(step.status)}</small>{step.status === 'blocked' && proof.length ? <small className="plan-blocker-evidence">阻塞证据 · {proof[0].summary}</small> : null}</div>
      </div>;
    })}</div>
  </section>;
}

function ChangeSummary({ events, loading, onOpen }: { events: AgentEvent[]; loading: string; onOpen: (event: AgentEvent) => void }) {
  const changes = events.filter((event) => ['write_file', 'edit_file'].includes(event.tool || '') && event.type === 'tool_result' && event.result?.ok && event.result?.snapshot_id);
  if (!changes.length) return <p className="panel-empty">暂无文件修改</p>;
  return <>{changes.slice(-4).map((event, index) => {
    const path = String(event.result?.path || 'file');
    const snapshotId = String(event.result?.snapshot_id || '');
    return <button className="change-card" key={`${snapshotId}-${index}`} onClick={() => onOpen(event)} disabled={loading === snapshotId}><span className="python-icon">{path.endsWith('.py') ? 'Py' : 'M'}</span><div><b>{path}</b><small><em>{loading === snapshotId ? '正在生成 Diff…' : event.tool === 'write_file' ? '已写入 · 查看 Diff' : '已编辑 · 查看 Diff'}</em></small></div><span>›</span></button>;
  })}</>;
}

function ApprovalDialog({ event, stopping, onDecision, onStop }: { event: AgentEvent; stopping: boolean; onDecision: (decision: 'allow' | 'allow_all' | 'reject') => void; onStop: () => void }) {
  return <div className="modal-backdrop"><section className={`approval-dialog ${event.force_manual ? 'high-risk' : ''}`} role="dialog" aria-modal="true" aria-labelledby="approval-title"><div className="approval-symbol">!</div><p className="eyebrow">{event.force_manual ? 'HIGH-RISK CONFIRMATION' : 'PERMISSION REQUIRED'}</p><h2 id="approval-title">{event.force_manual ? '确认执行高风险操作？' : '允许 Agent 执行此操作？'}</h2><div className="approval-operation"><span className="activity-icon">{toolIcon(event.tool)}</span><div><b>{toolLabel(event)}</b><small>{toolDetail(event)}</small></div></div>{event.risk_reason && <div className="risk-reason"><b>风险原因</b><span>{riskReasonLabel(event.risk_reason)}</span></div>}<p>{event.force_manual ? '自动审批不会跳过这一步。只有明确确认后，本次操作才会执行。' : '此操作可能修改工作区或执行本地命令。请确认内容符合你的预期。'}</p><div className="approval-actions"><button className="stop-approval" onClick={onStop} disabled={stopping}>{stopping ? '正在停止…' : '停止任务'}</button><button onClick={() => onDecision('reject')}>拒绝操作</button>{!event.force_manual && <button onClick={() => onDecision('allow_all')}>本次会话自动审批</button>}<button className="primary" onClick={() => onDecision('allow')}>允许一次</button></div></section></div>;
}

function DiffDialog({ view, onClose }: { view: DiffView; onClose: () => void }) {
  const lines = view.diff ? view.diff.split('\n') : [];
  return <div className="modal-backdrop"><section className="diff-dialog" role="dialog" aria-modal="true" aria-labelledby="diff-title"><header><div><p className="eyebrow">FILE CHANGES</p><h2 id="diff-title">{view.path}</h2></div><button className="dialog-close" type="button" onClick={onClose} aria-label="关闭 Diff">×</button></header>{lines.length ? <pre className="diff-content">{lines.map((line, index) => <span key={index} className={line.startsWith('+') && !line.startsWith('+++') ? 'added' : line.startsWith('-') && !line.startsWith('---') ? 'removed' : line.startsWith('@@') ? 'hunk' : ''}>{line || ' '}{'\n'}</span>)}</pre> : <div className="diff-empty">当前文件内容与修改前一致。</div>}{view.truncated && <footer>Diff 内容过长，已显示前 200,000 个字符。</footer>}</section></div>;
}

function ProjectPicker({ projects, directory, error, switching, onBrowse, onSelect, onClose }: { projects: Projects | null; directory: DirectoryListing | null; error: string; switching: boolean; onBrowse: (path?: string) => void; onSelect: (path: string) => void; onClose: () => void }) {
  return (
    <div className="modal-backdrop">
      <section className="project-dialog" role="dialog" aria-modal="true" aria-labelledby="project-title">
        <header>
          <div><p className="eyebrow">LOCAL WORKSPACES</p><h2 id="project-title">选择本地主机项目</h2><p>选择后，Agent 的文件和命令操作都会限制在该目录内。</p></div>
          <button className="dialog-close" type="button" onClick={onClose} aria-label="关闭项目选择器">×</button>
        </header>
        <div className="project-picker-body">
          <aside>
            <div className="picker-section-title">最近项目</div>
            <div className="recent-projects">
              {projects?.recent.length ? projects.recent.map((path) => (
                <button type="button" key={path} className={path === projects.current ? 'current' : ''} onClick={() => onSelect(path)} disabled={switching}>
                  <span>◇</span><div><b>{projectName(path)}</b><small>{path}</small></div>{path === projects.current && <em>当前</em>}
                </button>
              )) : <p>还没有最近项目</p>}
            </div>
            <button className="computer-button" type="button" onClick={() => onBrowse()}><span>▦</span><b>此电脑</b></button>
          </aside>
          <section className="directory-browser">
            <div className="directory-toolbar">
              <button type="button" onClick={() => directory?.parent ? onBrowse(directory.parent) : onBrowse()} disabled={!directory}>←</button>
              <div><small>当前文件夹</small><b>{directory?.current || '此电脑'}</b></div>
            </div>
            {error && <div className="picker-error">{error}</div>}
            <div className="directory-list">
              {directory?.entries.length ? directory.entries.map((entry) => (
                <button type="button" key={entry.path} onClick={() => onBrowse(entry.path)}>
                  <span>{entry.type === 'root' ? '▣' : '▾'}</span><div><b>{entry.name}</b><small>{entry.path}</small></div><em>›</em>
                </button>
              )) : <div className="picker-empty">{directory ? '该目录中没有子文件夹' : '正在读取本地目录…'}</div>}
            </div>
            <footer>
              <span>以后直接运行 <code>yukai --web</code> 将自动打开最近项目</span>
              <button className="select-folder" type="button" onClick={() => directory?.current && onSelect(directory.current)} disabled={!directory?.current || switching}>{switching ? '正在切换…' : '选择当前文件夹'}</button>
            </footer>
          </section>
        </div>
      </section>
    </div>
  );
}

function DemoTimeline() {
  return <>
    <article className="user-turn"><div className="avatar user-avatar">FY</div><div className="turn-body"><div className="turn-meta"><b>你</b><time>15:42:08</time></div><p>修复 slugify，使全部测试通过；不要修改测试，并在结束前运行完整测试。</p></div></article>
    <article className="agent-turn"><div className="avatar agent-avatar"><span>Y</span></div><div className="turn-body"><div className="turn-meta"><b>Yukai</b><span className="thinking-label">思考与执行</span><time>15:42:09</time></div><div className="activity-stack"><DemoActivity icon="⌕" title="浏览工作区" detail="发现 3 个文件" meta="84ms" /><DemoActivity icon="≡" title="读取 3 个文件" detail="README.md · slugify.py · test_slugify.py" meta="12ms" /><DemoActivity icon="›_" title="运行测试" detail="python -m unittest -v" meta="EXIT 1" warning /></div><div className="terminal-card"><div className="terminal-bar"><span /><span /><span /><b>TEST OUTPUT</b><button>复制</button></div><pre><span className="muted">Ran 5 tests in 0.006s</span>{'\n'}<span className="error">FAILED (failures=4)</span>{'\n'}<span className="muted">AssertionError: &apos;hello,-world!&apos; != &apos;hello-world&apos;</span></pre></div><div className="activity-stack second"><DemoActivity icon="±" title="编辑 slugify.py" detail="+12 −1 · 已创建恢复点" meta="M" /><DemoActivity icon="›_" title="重新运行测试" detail="5 passed · 0 failed" meta="PASS" /></div><div className="answer-card"><div className="answer-title"><span>✓</span><b>任务完成</b><small>6 个模型步骤</small></div><p>已修复 <code>slugify.py</code>：加入 Unicode 规范化，过滤标点并合并分隔符；空结果会抛出明确异常。测试文件未修改。</p><div className="verification"><span>✓</span><b>验证通过</b><code>Ran 5 tests · OK</code></div></div></div></article>
  </>;
}

function DemoActivity({ icon, title, detail, meta, warning = false }: { icon: string; title: string; detail: string; meta: string; warning?: boolean }) {
  return <div className={`activity done ${warning ? 'warning' : ''}`}><span className="activity-icon">{icon}</span><div><b>{title}</b><small>{detail}</small></div><span className={meta === 'PASS' ? 'pass-badge' : warning ? 'exit-badge' : 'duration'}>{meta}</span></div>;
}

function toolIcon(tool?: string) { return ({ list_files: '⌕', read_file: '≡', search_text: '⌕', write_file: '+', edit_file: '±', make_directory: '□', run_command: '›_', update_plan: '✓' } as Record<string, string>)[tool || ''] || '◆'; }
function toolLabel(event: AgentEvent) { const args = event.arguments || {}; const path = String(args.path || '.'); return ({ list_files: `浏览 ${path}`, read_file: `读取 ${path}`, search_text: `搜索 “${args.query || ''}”`, write_file: `写入 ${path}`, edit_file: `编辑 ${path}`, make_directory: `创建目录 ${path}`, run_command: `运行 ${compact(String(args.command || ''), 90)}`, update_plan: `更新计划 · ${compact(String(args.summary || '当前任务'), 60)}` } as Record<string, string>)[event.tool || ''] || String(event.tool || 'Agent 操作'); }
function toolDetail(event: AgentEvent) { const args = event.arguments || {}; if (event.tool === 'run_command') return String(args.command || ''); if (args.content_lines) return `${args.content_lines} 行内容`; if (args.old_text_lines || args.new_text_lines) return `替换 ${args.old_text_lines || 0} → ${args.new_text_lines || 0} 行`; return String(args.path || args.query || ''); }
function riskReasonLabel(reason: string) {
  return ({
    'command deletes files or directories': '该命令会删除文件或目录',
    'command can discard or overwrite Git data': '该命令可能丢弃或覆盖 Git 数据',
    'command requests elevated privileges': '该命令请求提升系统权限',
    'command changes permissions or system configuration': '该命令会修改权限或系统配置',
    'command downloads and executes remote content': '该命令会下载并执行远程内容',
    'command references a path outside the workspace': '该命令引用了工作区外的路径',
    'compound shell syntax can hide additional operations': '复合 shell 语法可能隐藏额外操作',
    'command is not on the automatic-execution allowlist': '该命令不在自动执行白名单中',
  } as Record<string, string>)[reason] || reason;
}
function createConversationSession(): ConversationSession {
  return {
    id: `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`,
    title: '新会话',
    events: [],
    updatedAt: Date.now(),
    pinned: false,
    archived: false,
    contextChars: 0,
    messageCount: 0,
    contextCompactions: 0,
  };
}
function sessionTime(updatedAt: number) {
  const elapsed = Math.max(0, Date.now() - updatedAt);
  if (elapsed < 60_000) return '刚刚';
  if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)} 分钟前`;
  if (elapsed < 86_400_000) return `${Math.floor(elapsed / 3_600_000)} 小时前`;
  return new Date(updatedAt).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
}
function compact(value: string, limit: number) { const normalized = value.replace(/\s+/g, ' ').trim(); return normalized.length <= limit ? normalized : `${normalized.slice(0, limit - 1)}…`; }
function formatChars(value: number) { if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}m chars`; if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 100_000 ? 0 : 1)}k chars`; return `${value} chars`; }
function planStatusLabel(status: PlanStep['status']) { return ({ pending: '等待执行', in_progress: '正在执行', completed: '证据已确认', blocked: '任务被阻塞' })[status]; }
function blockerTypeLabel(type?: PlanStep['blocker_type']) { return ({ tool_failure: '工具执行失败', missing_prerequisite: '缺少前置条件', environment: '环境限制', user_input_required: '等待用户输入' } as Record<string, string>)[type || ''] || '任务被阻塞'; }
function finalStatus(reason?: string) { if (reason === 'completed') return { icon: '✓', title: '完成', meta: '任务完成' }; if (reason === 'blocked') return { icon: '!', title: '任务被阻塞', meta: '需要处理' }; if (reason === 'incomplete_plan') return { icon: '!', title: '计划未完成', meta: '未完成' }; return { icon: '■', title: '任务已停止', meta: '已停止' }; }

function MarkdownText({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      const Heading = heading[1].length === 1 ? 'h3' : heading[1].length === 2 ? 'h4' : 'h5';
      blocks.push(<Heading key={`h-${index}`}>{renderInlineMarkdown(heading[2])}</Heading>);
      index += 1;
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*[-*]\s+/, ''));
        index += 1;
      }
      blocks.push(<ul key={`ul-${index}`}>{items.map((item, itemIndex) => <li key={itemIndex}>{renderInlineMarkdown(item)}</li>)}</ul>);
      continue;
    }
    const paragraph: string[] = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !/^(#{1,3})\s+/.test(lines[index]) && !/^\s*[-*]\s+/.test(lines[index])) {
      paragraph.push(lines[index]);
      index += 1;
    }
    blocks.push(<p key={`p-${index}`}>{paragraph.map((item, itemIndex) => <span key={itemIndex}>{itemIndex > 0 && <br />}{renderInlineMarkdown(item)}</span>)}</p>);
  }
  return <div className="answer-markdown">{blocks}</div>;
}

function renderInlineMarkdown(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean).map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith('`') && part.endsWith('`')) return <code key={index}>{part.slice(1, -1)}</code>;
    return part;
  });
}
function projectName(path: string) { return path.split(/[\\/]/).filter(Boolean).at(-1) || path; }
function now() { return new Date().toLocaleTimeString('zh-CN', { hour12: false }); }
