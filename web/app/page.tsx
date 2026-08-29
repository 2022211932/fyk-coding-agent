'use client';

import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';

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
  timestamp?: string;
};

type Status = {
  version: string;
  model: string;
  workspace: string;
  automatic_approval: boolean;
};

type FileEntry = { path: string; type: string; size?: number };
type DirectoryEntry = { name: string; path: string; type: 'directory' | 'root' };
type DirectoryListing = {
  current: string | null;
  parent: string | null;
  entries: DirectoryEntry[];
};
type Projects = { current: string; recent: string[]; roots: DirectoryEntry[] };

const demoSessions = [
  { title: '修复 slugify 测试', time: '刚刚', active: true },
  { title: '实现 01 背包算法', time: '12 分钟前', active: false },
  { title: '检查项目安全边界', time: '昨天', active: false },
];

export default function Home() {
  const [prompt, setPrompt] = useState('');
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [running, setRunning] = useState(false);
  const [connectionError, setConnectionError] = useState('');
  const [approval, setApproval] = useState<AgentEvent | null>(null);
  const [projectPickerOpen, setProjectPickerOpen] = useState(false);
  const [projects, setProjects] = useState<Projects | null>(null);
  const [directory, setDirectory] = useState<DirectoryListing | null>(null);
  const [pickerError, setPickerError] = useState('');
  const [switchingWorkspace, setSwitchingWorkspace] = useState(false);
  const [sessionId] = useState(() => `web-${Date.now().toString(36)}`);
  const connection = useRef({ api: '', token: '' });
  const timelineEnd = useRef<HTMLDivElement>(null);

  const live = Boolean(status);
  const toolCalls = events.filter((event) => event.type === 'tool_call').length;
  const latestStep = events.reduce((max, event) => Math.max(max, event.step || event.steps || 0), 0);
  const currentTitle = useMemo(() => {
    const first = events.find((event) => event.type === 'user')?.message;
    return first ? compact(first, 34) : live ? '新会话' : '修复 slugify 测试';
  }, [events, live]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const api = params.get('api') || window.sessionStorage.getItem('fyk-api');
    const token = params.get('token') || window.sessionStorage.getItem('fyk-token');
    if (!api || !token) return;
    window.sessionStorage.setItem('fyk-api', api);
    window.sessionStorage.setItem('fyk-token', token);
    window.history.replaceState({}, '', window.location.pathname);
    connection.current = { api, token };
    const connect = async () => {
      try {
        const headers = { 'X-FYK-Token': token };
        const response = await fetch(`${api}/api/status`, { headers });
        if (!response.ok) throw new Error(`连接失败 (${response.status})`);
        setStatus(await response.json());
        const fileResponse = await fetch(`${api}/api/files?path=.`, { headers });
        const fileData = await fileResponse.json();
        if (fileData.ok) setFiles(fileData.entries || []);
      } catch (error) {
        setConnectionError(error instanceof Error ? error.message : '无法连接本地 Agent');
      }
    };
    void connect();
  }, []);

  useEffect(() => {
    timelineEnd.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [events]);

  async function refreshFiles() {
    const { api, token } = connection.current;
    if (!api) return;
    const response = await fetch(`${api}/api/files?path=.`, { headers: { 'X-FYK-Token': token } });
    const data = await response.json();
    if (data.ok) setFiles(data.entries || []);
  }

  async function toggleAutomaticApproval() {
    if (!status || running) return;
    const { api, token } = connection.current;
    try {
      const response = await fetch(`${api}/api/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-FYK-Token': token },
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
        headers: { 'X-FYK-Token': token },
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
        headers: { 'X-FYK-Token': token },
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
        headers: { 'Content-Type': 'application/json', 'X-FYK-Token': token },
        body: JSON.stringify({ path }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '无法切换工作区');
      setStatus(data);
      setEvents([]);
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
      setConnectionError('请使用 fyk-agent --web 启动实时控制台。当前页面为交互演示。');
      return;
    }
    setPrompt('');
    setRunning(true);
    setConnectionError('');
    setEvents((previous) => [...previous, { type: 'user', message, timestamp: now() }]);
    const { api, token } = connection.current;
    try {
      const response = await fetch(`${api}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-FYK-Token': token },
        body: JSON.stringify({ message, session_id: sessionId }),
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
          setEvents((previous) => [...previous, item]);
        }
        if (done) break;
      }
      await refreshFiles();
    } catch (error) {
      const messageText = error instanceof Error ? error.message : 'Agent 请求失败';
      setEvents((previous) => [...previous, { type: 'error', error: messageText, timestamp: now() }]);
    } finally {
      setRunning(false);
    }
  }

  async function decide(decision: 'allow' | 'allow_all' | 'reject') {
    if (!approval?.approval_id) return;
    const { api, token } = connection.current;
    await fetch(`${api}/api/approvals/${approval.approval_id}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-FYK-Token': token },
      body: JSON.stringify({ decision }),
    });
    setEvents((previous) => [...previous, { type: 'approval_decision', message: decision, timestamp: now() }]);
    setApproval(null);
    if (decision === 'allow_all' && status) setStatus({ ...status, automatic_approval: true });
  }

  async function clearSession() {
    if (!live || running) return;
    const { api, token } = connection.current;
    await fetch(`${api}/api/clear`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-FYK-Token': token },
      body: JSON.stringify({ session_id: sessionId }),
    });
    setEvents([]);
  }

  async function undo() {
    if (!live || running) return;
    const { api, token } = connection.current;
    const response = await fetch(`${api}/api/undo`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-FYK-Token': token },
      body: '{}',
    });
    const result = await response.json();
    setEvents((previous) => [...previous, {
      type: result.ok ? 'notice' : 'error',
      message: result.ok ? `已恢复 ${result.path}` : undefined,
      error: result.ok ? undefined : result.error,
      timestamp: now(),
    }]);
    await refreshFiles();
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
        <div className="brand"><span className="brand-mark" aria-hidden="true"><i /></span><span>FYK <b>Agent</b></span><span className="version">v{status?.version || '0.2'}</span></div>
        <button className="workspace-pill" type="button" onClick={openProjectPicker} disabled={!live || running} title="选择本地主机上的项目"><span className={`status-dot ${connectionError ? 'offline' : ''}`} /><span className="workspace-path">{live ? compact(status.workspace, 52) : 'Demo · demo-workspace'}</span><span className="workspace-chevron">⌄</span></button>
        <div className="top-actions"><button className="icon-button" aria-label="撤销最近修改" onClick={undo}>↶</button><div className="model-chip"><span>◆</span> {status?.model || 'DeepSeek V4 Pro'}</div></div>
      </header>

      <div className="workbench">
        <aside className="session-rail">
          <button className="new-session" onClick={clearSession}><span>＋</span> 新建会话</button>
          <p className="rail-label">最近任务</p>
          <nav aria-label="最近任务">
            {(live ? [{ title: currentTitle, time: running ? '运行中' : '当前会话', active: true }] : demoSessions).map((session) => (
              <button key={session.title} className={`session-item ${session.active ? 'active' : ''}`}>
                <span className="session-glyph">{session.active ? '●' : '○'}</span><span className="session-copy"><b>{session.title}</b><small>{session.time}</small></span>{session.active && <span className="more">···</span>}
              </button>
            ))}
          </nav>
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
              <div className="empty-state"><span className="brand-mark"><i /></span><p className="eyebrow">READY</p><h2>把编程任务交给 FYK Agent</h2><p>你将实时看到模型思考、工具调用、命令输出、文件变更与审批请求。</p><div><button onClick={() => setPrompt('阅读项目结构，告诉我应该从哪里开始。')}>了解项目</button><button onClick={() => setPrompt('运行测试，定位失败原因并提出修复方案。')}>检查测试</button></div></div>
            )}
            <div ref={timelineEnd} />
          </div>

          <form className="composer" onSubmit={submit}>
            {running && <div className="queue-toast"><span />Agent 正在执行任务</div>}
            <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder={live ? '继续交给 FYK Agent 一个任务…' : '输入任务体验交互效果…'} aria-label="输入任务" rows={2} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} />
            <div className="composer-foot"><div><button type="button" className="mini-button">＋</button><span><kbd>Shift</kbd> + <kbd>Enter</kbd> 换行</span></div><div><button type="button" className={`approval-switch ${status?.automatic_approval ? 'enabled' : ''}`} role="switch" aria-checked={Boolean(status?.automatic_approval)} onClick={toggleAutomaticApproval} disabled={!live || running} title="开启后，写文件和运行命令将不再逐次询问"><span><i /></span>{status?.automatic_approval ? '自动审批' : '手动审批'}</button><button className="send" type="submit" disabled={!prompt.trim() || running}>{running ? '执行中…' : '运行任务'} <span>↵</span></button></div></div>
          </form>
        </section>

        <aside className="inspector">
          <section className="inspector-section context-section">
            <div className="panel-heading"><span>会话上下文</span><b>{live ? `${Math.min(events.length * 2, 99)}%` : '24%'}</b></div><div className="meter"><i style={{ width: `${live ? Math.min(events.length * 2, 99) : 24}%` }} /></div><div className="meter-label"><span>{events.length || 12} events</span><span>800k chars</span></div>
            <div className="stat-grid"><div><b>{live ? latestStep : 6}</b><span>模型步骤</span></div><div><b>{live ? toolCalls : 7}</b><span>工具调用</span></div><div><b>{events.filter((item) => item.type === 'final').length}</b><span>完成任务</span></div></div>
          </section>
          <section className="inspector-section"><div className="panel-heading"><span>文件变更</span><small>{events.filter((item) => item.tool === 'edit_file' || item.tool === 'write_file').length || (live ? 0 : 1)} FILE</small></div>{live ? <ChangeSummary events={events} /> : <><button className="change-card"><span className="python-icon">Py</span><div><b>slugify.py</b><small><em>+12</em> <del>−1</del></small></div><span>›</span></button><button className="diff-button">查看完整 Diff <span>↗</span></button></>}</section>
          <section className="inspector-section file-section"><div className="panel-heading"><span>工作区</span><button onClick={refreshFiles}>↻</button></div><div className="file-tree">{shownFiles.map((file) => <div key={file.path}><span>{file.type === 'directory' ? '▾' : file.path.endsWith('.py') ? 'Py' : '≡'}</span><b>{file.path}</b>{events.some((event) => String(event.arguments?.path || '') === file.path && ['write_file', 'edit_file'].includes(event.tool || '')) && <em>M</em>}</div>)}</div></section>
          <section className="safety-card"><span className="shield">◇</span><div><b>工作区隔离已开启</b><small>{live ? '随机令牌 · 仅限 127.0.0.1' : '演示模式未连接本地文件'}</small></div></section>
        </aside>
      </div>

      {approval && <ApprovalDialog event={approval} onDecision={decide} />}
      {projectPickerOpen && <ProjectPicker projects={projects} directory={directory} error={pickerError} switching={switchingWorkspace} onBrowse={browseDirectory} onSelect={selectWorkspace} onClose={() => setProjectPickerOpen(false)} />}
    </main>
  );
}

function LiveTimeline({ events, running }: { events: AgentEvent[]; running: boolean }) {
  return <>{events.map((event, index) => {
    if (event.type === 'run_started') return null;
    if (event.type === 'user') return <article className="user-turn" key={index}><div className="avatar user-avatar">FY</div><div className="turn-body"><div className="turn-meta"><b>你</b><time>{event.timestamp}</time></div><p>{event.message}</p></div></article>;
    if (event.type === 'model_request') return <div className="live-activity thinking-row" key={index}><span className="pulse" /><div><b>DeepSeek 正在思考</b><small>模型步骤 {event.step}</small></div></div>;
    if (event.type === 'tool_call') return <div className="live-activity" key={index}><span className="activity-icon">{toolIcon(event.tool)}</span><div><b>{toolLabel(event)}</b><small>{toolDetail(event)}</small></div><span className="running-dot" /></div>;
    if (event.type === 'tool_result') return <ToolResult event={event} key={index} />;
    if (event.type === 'approval_required') return <div className="live-activity approval-row" key={index}><span className="activity-icon">!</span><div><b>等待操作审批</b><small>{toolLabel(event)}</small></div><span className="exit-badge">REVIEW</span></div>;
    if (event.type === 'approval_decision') return <div className="notice-row" key={index}>审批结果：{event.message}</div>;
    if (event.type === 'final') return <article className="agent-turn live-answer" key={index}><div className="avatar agent-avatar"><span>◆</span></div><div className="turn-body"><div className="turn-meta"><b>FYK Agent</b><span className="thinking-label">任务完成</span><time>{event.timestamp}</time></div><div className="answer-card"><div className="answer-title"><span>✓</span><b>{event.stop_reason === 'completed' ? '完成' : '已停止'}</b><small>{event.steps} 个模型步骤</small></div><p className="answer-text">{event.text}</p></div></div></article>;
    if (event.type === 'notice') return <div className="notice-row success" key={index}>{event.message}</div>;
    if (event.type === 'error') return <div className="connection-banner" key={index}>{event.error}</div>;
    return null;
  })}{running && events.at(-1)?.type !== 'model_request' && <div className="live-activity thinking-row"><span className="pulse" /><div><b>Agent 正在继续</b><small>等待下一个事件</small></div></div>}</>;
}

function ToolResult({ event }: { event: AgentEvent }) {
  const result = event.result || {};
  const ok = Boolean(result.ok);
  const output = String(result.stdout || result.stderr || '');
  const detail = result.duration_ms
    ? `${Math.round(Number(result.duration_ms))}ms`
    : result.error
      ? String(result.error)
      : result.exit_code !== undefined
        ? `exit ${result.exit_code}`
        : '';
  return <div className={`tool-result-row ${ok ? 'ok' : 'failed'}`}><span>{ok ? '✓' : '×'}</span><div><b>{ok ? '执行成功' : '执行失败'} · {event.tool}</b><small>{detail}</small>{output && <pre>{compact(output, 1600)}</pre>}</div></div>;
}

function ChangeSummary({ events }: { events: AgentEvent[] }) {
  const changes = events.filter((event) => ['write_file', 'edit_file'].includes(event.tool || '') && event.type === 'tool_call');
  if (!changes.length) return <p className="panel-empty">暂无文件修改</p>;
  return <>{changes.slice(-4).map((event, index) => <button className="change-card" key={index}><span className="python-icon">{String(event.arguments?.path || '').endsWith('.py') ? 'Py' : 'M'}</span><div><b>{String(event.arguments?.path || 'file')}</b><small><em>{event.tool === 'write_file' ? '已写入' : '已编辑'}</em></small></div><span>›</span></button>)}</>;
}

function ApprovalDialog({ event, onDecision }: { event: AgentEvent; onDecision: (decision: 'allow' | 'allow_all' | 'reject') => void }) {
  return <div className="modal-backdrop"><section className="approval-dialog" role="dialog" aria-modal="true" aria-labelledby="approval-title"><div className="approval-symbol">!</div><p className="eyebrow">PERMISSION REQUIRED</p><h2 id="approval-title">允许 Agent 执行此操作？</h2><div className="approval-operation"><span className="activity-icon">{toolIcon(event.tool)}</span><div><b>{toolLabel(event)}</b><small>{toolDetail(event)}</small></div></div><p>此操作可能修改工作区或执行本地命令。请确认内容符合你的预期。</p><div className="approval-actions"><button onClick={() => onDecision('reject')}>拒绝</button><button onClick={() => onDecision('allow_all')}>本次会话全部允许</button><button className="primary" onClick={() => onDecision('allow')}>允许一次</button></div></section></div>;
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
              <span>以后直接运行 <code>fyk-agent --web</code> 将自动打开最近项目</span>
              <button className="select-folder" type="button" onClick={() => directory?.current && onSelect(directory.current)} disabled={!directory?.current || switching}>{switching ? '正在切换…' : '选择当前文件夹'}</button>
            </footer>
          </section>
        </div>
      </section>
    </div>
  );
}

function DemoTimeline() {
  return <><article className="user-turn"><div className="avatar user-avatar">FY</div><div className="turn-body"><div className="turn-meta"><b>你</b><time>15:42:08</time></div><p>修复 slugify，使全部测试通过；不要修改测试，并在结束前运行完整测试。</p></div></article><article className="agent-turn"><div className="avatar agent-avatar"><span>◆</span></div><div className="turn-body"><div className="turn-meta"><b>FYK Agent</b><span className="thinking-label">思考与执行</span><time>15:42:09</time></div><div className="activity-stack"><DemoActivity icon="⌕" title="浏览工作区" detail="发现 3 个文件" meta="84ms" /><DemoActivity icon="≡" title="读取 3 个文件" detail="README.md · slugify.py · test_slugify.py" meta="12ms" /><DemoActivity icon="›_" title="运行测试" detail="python -m unittest -v" meta="EXIT 1" warning /></div><div className="terminal-card"><div className="terminal-bar"><span /><span /><span /><b>TEST OUTPUT</b><button>复制</button></div><pre><span className="muted">Ran 5 tests in 0.006s</span>{'\n'}<span className="error">FAILED (failures=4)</span>{'\n'}<span className="muted">AssertionError: &apos;hello,-world!&apos; != &apos;hello-world&apos;</span></pre></div><div className="activity-stack second"><DemoActivity icon="±" title="编辑 slugify.py" detail="+12 −1 · 已创建恢复点" meta="M" /><DemoActivity icon="›_" title="重新运行测试" detail="5 passed · 0 failed" meta="PASS" /></div><div className="answer-card"><div className="answer-title"><span>✓</span><b>任务完成</b><small>6 个模型步骤</small></div><p>已修复 <code>slugify.py</code>：加入 Unicode 规范化，过滤标点并合并分隔符；空结果会抛出明确异常。测试文件未修改。</p><div className="verification"><span>✓</span><b>验证通过</b><code>Ran 5 tests · OK</code></div></div></div></article></>;
}

function DemoActivity({ icon, title, detail, meta, warning = false }: { icon: string; title: string; detail: string; meta: string; warning?: boolean }) {
  return <div className={`activity done ${warning ? 'warning' : ''}`}><span className="activity-icon">{icon}</span><div><b>{title}</b><small>{detail}</small></div><span className={meta === 'PASS' ? 'pass-badge' : warning ? 'exit-badge' : 'duration'}>{meta}</span></div>;
}

function toolIcon(tool?: string) { return ({ list_files: '⌕', read_file: '≡', search_text: '⌕', write_file: '+', edit_file: '±', make_directory: '□', run_command: '›_' } as Record<string, string>)[tool || ''] || '◆'; }
function toolLabel(event: AgentEvent) { const args = event.arguments || {}; const path = String(args.path || '.'); return ({ list_files: `浏览 ${path}`, read_file: `读取 ${path}`, search_text: `搜索 “${args.query || ''}”`, write_file: `写入 ${path}`, edit_file: `编辑 ${path}`, make_directory: `创建目录 ${path}`, run_command: `运行 ${compact(String(args.command || ''), 90)}` } as Record<string, string>)[event.tool || ''] || String(event.tool || 'Agent 操作'); }
function toolDetail(event: AgentEvent) { const args = event.arguments || {}; if (event.tool === 'run_command') return String(args.command || ''); if (args.content_lines) return `${args.content_lines} 行内容`; if (args.old_text_lines || args.new_text_lines) return `替换 ${args.old_text_lines || 0} → ${args.new_text_lines || 0} 行`; return String(args.path || args.query || ''); }
function compact(value: string, limit: number) { const normalized = value.replace(/\s+/g, ' ').trim(); return normalized.length <= limit ? normalized : `${normalized.slice(0, limit - 1)}…`; }
function projectName(path: string) { return path.split(/[\\/]/).filter(Boolean).at(-1) || path; }
function now() { return new Date().toLocaleTimeString('zh-CN', { hour12: false }); }
