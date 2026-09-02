'use client';

import { FormEvent, useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react';

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
  engineering?: EngineeringState;
  question?: EngineeringQuestion;
};

type PlanEvidence = { id: string; tool: string; ok: boolean; summary: string; step: number; verification: boolean; error_type?: string };
type PlanStep = { id: string; title: string; kind: 'inspect' | 'change' | 'verify' | 'other'; status: 'pending' | 'in_progress' | 'completed' | 'blocked'; evidence_ids: string[]; note: string; blocker_type?: 'tool_failure' | 'missing_prerequisite' | 'environment' | 'user_input_required' };
type TaskPlan = { summary: string; steps: PlanStep[]; completed: number; total: number; terminal: boolean; blocked: boolean; evidence: PlanEvidence[] };
type EngineeringOption = { id: string; label: string; description?: string; requires_input?: boolean; input_placeholder?: string };
type EngineeringReview = { requirements: number; design_modules: number; implementation_links: number; verification_links: number; stale_evidence: number; residual_risk: string; requirements_covered?: number; criteria_total?: number; criteria_covered?: number; black_box_links?: number; white_box_links?: number; deliverables?: string[]; modules_completed?: number; modules_total?: number; incomplete_modules?: ImplementationModuleAudit[]; invalid_module_links?: InvalidModuleLink[]; untracked_files?: string[] };
type EngineeringWorkspaceReview = { project_title: string; requirements: number; workspace: string; warning: string };
type EngineeringRequirement = { id: string; title: string; kind: 'functional' | 'non_functional'; description: string; acceptance_criteria: string[] };
type UseCaseActor = { id: string; name: string; description: string };
type UseCaseAlternativeFlow = { id: string; condition: string; steps: string[] };
type UseCaseAcceptanceLink = { requirement_id: string; criterion_indices: number[] };
type EngineeringUseCase = { id: string; name: string; goal: string; actor_ids: string[]; preconditions: string[]; main_flow: string[]; alternative_flows: UseCaseAlternativeFlow[]; postconditions: string[]; acceptance_links: UseCaseAcceptanceLink[]; requirement_ids?: string[] };
type UseCaseRelationship = { from: string; to: string; type: 'include' | 'extend' | 'generalization'; label?: string };
type EngineeringBaselineReview = { requirements: EngineeringRequirement[]; assumptions: string[]; actors?: UseCaseActor[]; use_cases?: EngineeringUseCase[]; use_case_relationships?: UseCaseRelationship[]; digest: string };
type EngineeringModule = { id: string; name: string; responsibility: string; requirement_ids: string[]; interfaces: string[]; dependencies?: string[] };
type UmlClass = { id: string; name: string; stereotype?: string; attributes: string[]; methods: string[]; requirement_ids: string[] };
type UmlRelationship = { from: string; to: string; type: 'association' | 'inheritance' | 'composition' | 'aggregation' | 'dependency'; label?: string };
type SequenceStep = { from: string; to: string; message: string; response?: boolean };
type EngineeringSequence = { id: string; name: string; requirement_ids: string[]; participants: string[]; steps: SequenceStep[] };
type ProcessFlowNode = { id: string; type: 'start' | 'process' | 'decision' | 'input_output' | 'end'; label: string };
type ProcessFlowEdge = { from: string; to: string; label?: string };
type ProcessFlow = { id: string; name: string; requirement_ids: string[]; direction?: 'TD' | 'LR'; nodes: ProcessFlowNode[]; edges: ProcessFlowEdge[] };
type DomainObject = { id: string; name: string; kind: 'aggregate_root' | 'entity' | 'value_object' | 'domain_service' | 'repository'; description: string; business_rules: string[]; requirement_ids: string[] };
type InvalidModuleLink = { requirement_id: string; module_id: string; path: string };
type ImplementationModuleAudit = { id: string; name: string; required_requirement_ids: string[]; covered_requirement_ids: string[]; missing_requirement_ids: string[]; paths: string[]; complete: boolean };
type ImplementationAudit = { passed: boolean; modules_total: number; modules_completed: number; modules: ImplementationModuleAudit[]; incomplete_modules: ImplementationModuleAudit[]; uncovered_requirements: string[]; invalid_module_links: InvalidModuleLink[]; tracked_files: string[]; changed_files: string[]; untracked_files: string[] };
type TestCaseTrace = { requirement_id: string; criterion_indices: number[] };
type VerificationTestCase = { id: string; name: string; suite: string; path: string; line?: number | null; method: 'black_box' | 'white_box' | 'supporting' | 'unclassified'; level: string; purpose: string; status: 'passed' | 'failed' | 'error' | 'skipped' | 'unknown'; detail?: string; traces?: TestCaseTrace[] };
type TestMethodStats = { total: number; passed: number; failed: number; errors: number; skipped: number; unknown: number };
type VerificationRun = { framework: string; command: string; status: 'passed' | 'failed'; total: number; passed: number; failed: number; errors: number; skipped: number; duration_seconds?: number | null; exit_code?: number | null; source: string; classified_cases: number; black_box: TestMethodStats; white_box: TestMethodStats; supporting?: TestMethodStats; unclassified: TestMethodStats; cases: VerificationTestCase[] };
type SupportingCheck = { requirement_id: string; kind: string; claim: string; command: string; criterion_indices: number[] };
type VerificationSummary = { latest_run?: VerificationRun | null; dynamic_trace_links: number; supporting_checks: number; supporting_items: SupportingCheck[] };
type TestStrategyAudit = { required: boolean; passed: boolean; missing: string[]; functional_criteria_total: number; functional_criteria_black_box_covered: number; core_modules_total: number; core_modules_white_box_covered: number; missing_black_box_criteria: string[]; missing_white_box_modules: string[]; dynamic_links_without_cases: string[]; misclassified_supporting_links: string[]; black_box_cases: number; white_box_cases: number };
type EngineeringDesignReview = { modules: EngineeringModule[]; uml_classes: UmlClass[]; uml_relationships: UmlRelationship[]; sequences: EngineeringSequence[]; process_flows?: ProcessFlow[]; domain_objects: DomainObject[]; digest: string };
type EngineeringQuestion = { question_id: string; decision_key: string; question: string; reason: string; options: EngineeringOption[]; baseline_review?: EngineeringBaselineReview; design_review?: EngineeringDesignReview; review_summary?: EngineeringReview; workspace_review?: EngineeringWorkspaceReview };
type EngineeringPhase = { id: string; title: string; status: 'pending' | 'active' | 'awaiting_user' | 'completed'; gate: { passed: boolean; missing: string[] } };
type EngineeringState = {
  phase: string;
  status: string;
  project_title: string;
  requirements: EngineeringRequirement[];
  assumptions?: string[];
  actors?: UseCaseActor[];
  use_cases?: EngineeringUseCase[];
  use_case_relationships?: UseCaseRelationship[];
  design_modules: EngineeringModule[];
  uml_classes?: UmlClass[];
  uml_relationships?: UmlRelationship[];
  sequences?: EngineeringSequence[];
  process_flows?: ProcessFlow[];
  domain_objects?: DomainObject[];
  implementation_links: Array<{ requirement_id: string; module_ids?: string[]; path: string }>;
  implementation_audit?: ImplementationAudit;
  test_links: Array<{ requirement_id: string; command: string; evidence_kind?: string; test_method?: 'black_box' | 'white_box'; test_level?: string; claim?: string; criterion_indices?: number[]; test_case_ids?: string[]; module_ids?: string[] }>;
  verification_summary?: VerificationSummary;
  test_strategy_audit?: TestStrategyAudit;
  decisions?: Array<{ key: string; option_id: string; option_label: string; decided_at: string }>;
  pending_question?: EngineeringQuestion | null;
  phases: EngineeringPhase[];
  active_skill?: { id: string; title: string; description: string };
};

type Status = {
  version: string;
  model: string;
  workspace: string;
  automatic_approval: boolean;
  max_context_chars: number;
  engineering: EngineeringState;
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
  engineeringMode: boolean;
};
type ConversationStore = { items: ConversationSession[]; activeId: string };
type DiffView = { path: string; diff: string; truncated: boolean };
type EngineeringArtifactPhase = 'requirements' | 'design' | 'implementation' | 'verification' | 'acceptance';

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
  const [engineeringArtifact, setEngineeringArtifact] = useState<EngineeringArtifactPhase | null>(null);
  const connection = useRef({ api: '', token: '' });
  const timelineEnd = useRef<HTMLDivElement>(null);

  const live = Boolean(status);
  const activeSession = conversationStore.items.find((session) => session.id === conversationStore.activeId) || conversationStore.items[0];
  const events = activeSession.events;
  const sessionId = activeSession.id;
  const toolCalls = events.filter((event) => event.type === 'tool_call').length;
  const latestStep = events.reduce((max, event) => Math.max(max, event.step || event.steps || 0), 0);
  const fileChanges = events.filter((event) => event.type === 'tool_result' && ['edit_file', 'write_file'].includes(event.tool || '') && event.result?.ok && !event.result?.unchanged);
  const currentTitle = live ? activeSession.title : '修复 slugify 测试';
  const orderedSessions = [...conversationStore.items].sort((left, right) => Number(right.pinned) - Number(left.pinned) || right.updatedAt - left.updatedAt);
  const recentSessions = orderedSessions.filter((session) => !session.archived);
  const archivedSessions = orderedSessions.filter((session) => session.archived);
  const contextPercent = status ? Math.min(100, (activeSession.contextChars / status.max_context_chars) * 100) : 24;
  const latestPlanEvent = [...events].reverse().find((event) => ['plan_updated', 'plan_reset'].includes(event.type));
  const activePlan = latestPlanEvent?.type === 'plan_updated' ? latestPlanEvent.plan : undefined;
  const latestEngineeringEvent = [...events].reverse().find((event) => event.type === 'engineering_state' && event.engineering);
  const activeEngineering = status?.engineering || latestEngineeringEvent?.engineering;

  function updateSession(targetId: string, update: (session: ConversationSession) => ConversationSession) {
    setConversationStore((previous) => ({
      ...previous,
      items: previous.items.map((session) => session.id === targetId ? update(session) : session),
    }));
  }

  function appendEvent(targetId: string, event: AgentEvent) {
    if (event.type === 'engineering_state' && event.engineering) {
      setStatus((current) => current ? { ...current, engineering: event.engineering } : current);
    }
    updateSession(targetId, (session) => ({
      ...session,
      events: [
        ...(event.type === 'context_stats' || event.type === 'engineering_state'
          ? session.events.filter((item) => item.type !== event.type)
          : session.events),
        event,
      ],
      updatedAt: Date.now(),
      contextChars: event.context_chars ?? session.contextChars,
      messageCount: event.message_count ?? session.messageCount,
      contextCompactions: event.context_compactions ?? session.contextCompactions,
    }));
  }

  const persistSession = useCallback(async (session: ConversationSession, fields?: Partial<Pick<ConversationSession, 'title' | 'pinned' | 'archived' | 'engineeringMode'>>) => {
    const { api, token } = connection.current;
    if (!api) return;
    const normalized = fields ? { ...fields, engineering_mode: fields.engineeringMode } : {};
    delete (normalized as Record<string, unknown>).engineeringMode;
    const payload = { session_id: session.id, ...normalized };
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
      engineeringMode: Boolean(item.engineering_mode),
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

  async function refreshStatus() {
    const { api, token } = connection.current;
    if (!api) return;
    try {
      const response = await fetch(`${api}/api/status`, { headers: { 'X-Yukai-Token': token } });
      if (!response.ok) return;
      const data = await response.json();
      if (data.ok) setStatus(data);
    } catch {
      // The streamed engineering_state event remains authoritative when this safety refresh is unavailable.
    }
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

  async function toggleEngineeringMode() {
    if (!live || running) return;
    const enabled = !activeSession.engineeringMode;
    updateSession(sessionId, (session) => ({ ...session, engineeringMode: enabled }));
    try {
      await persistSession(activeSession, { engineeringMode: enabled });
    } catch (error) {
      updateSession(sessionId, (session) => ({ ...session, engineeringMode: !enabled }));
      setConnectionError(error instanceof Error ? error.message : '无法切换软件工程模式');
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
      await refreshStatus();
      await refreshFiles();
    } catch (error) {
      setPickerError(error instanceof Error ? error.message : '无法切换工作区');
    } finally {
      setSwitchingWorkspace(false);
    }
  }

  async function runMessage(message: string, engineeringAnswer?: { question_id: string; option_id: string; answer?: string }) {
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
      events: [...session.events, { type: engineeringAnswer ? 'engineering_decision' : 'user', message, timestamp: now() }],
      updatedAt: Date.now(),
    }));
    const { api, token } = connection.current;
    try {
      const response = await fetch(`${api}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Yukai-Token': token },
        body: JSON.stringify({ message, session_id: runSessionId, engineering_mode: activeSession.engineeringMode, engineering_answer: engineeringAnswer }),
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
      await refreshStatus();
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

  async function submit(event: FormEvent) {
    event.preventDefault();
    const message = prompt.trim();
    if (!message || running) return;
    setPrompt('');
    await runMessage(message);
  }

  async function answerEngineeringQuestion(question: EngineeringQuestion, option: EngineeringOption, answer = '') {
    if (running) return;
    const detail = answer.trim();
    const message = `关于“${question.question}”，我的选择是“${option.label}”。${detail ? `具体说明：${detail}。` : ''}请记录该决策并继续软件工程流程。`;
    await runMessage(message, { question_id: question.question_id, option_id: option.id, answer: detail || option.label });
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
        <div className="brand"><span className="brand-mark" aria-hidden="true"><i /></span><span>Yukai</span><span className="version">v{status?.version || '0.3.3'}</span></div>
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
            {live ? <LiveTimeline events={events} running={running} onAnswerQuestion={answerEngineeringQuestion} /> : <DemoTimeline />}
            {live && events.length === 0 && (
              <div className="empty-state"><span className="brand-mark"><i /></span><p className="eyebrow">READY</p><h2>把编程任务交给 Yukai</h2><p>你将实时看到模型思考、工具调用、命令输出、文件变更与审批请求。</p><div><button onClick={() => setPrompt('阅读项目结构，告诉我应该从哪里开始。')}>了解项目</button><button onClick={() => setPrompt('运行测试，定位失败原因并提出修复方案。')}>检查测试</button></div></div>
            )}
            <div ref={timelineEnd} />
          </div>

          <form className="composer" onSubmit={submit}>
            {running && <div className="queue-toast"><span />Agent 正在执行任务</div>}
            <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder={live ? '继续交给 Yukai 一个任务…' : '输入任务体验交互效果…'} aria-label="输入任务" rows={2} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} />
            <div className="composer-foot"><div><button type="button" className={`engineering-toggle ${activeSession.engineeringMode ? 'enabled' : ''}`} onClick={toggleEngineeringMode} disabled={!live || running} title="按需求分析、设计、实现、测试和验收的质量门执行"><span>SE</span>{activeSession.engineeringMode ? '软件工程模式' : '快速模式'}</button><span><kbd>Shift</kbd> + <kbd>Enter</kbd> 换行</span></div><div><button type="button" className={`approval-switch ${status?.automatic_approval ? 'enabled' : ''}`} role="switch" aria-checked={Boolean(status?.automatic_approval)} onClick={toggleAutomaticApproval} disabled={!live || running} title="安全操作可自动通过，高风险命令始终需要人工确认"><span><i /></span>{status?.automatic_approval ? '安全操作自动审批' : '手动审批'}</button>{running ? <button className="stop-task" type="button" onClick={stopTask} disabled={stopping}><span>■</span>{stopping ? '正在停止…' : '停止任务'}</button> : <button className="send" type="submit" disabled={!prompt.trim()}>运行任务 <span>↵</span></button>}</div></div>
          </form>
        </section>

        <aside className="inspector">
          {live && activeSession.engineeringMode && activeEngineering && <EngineeringPanel engineering={activeEngineering} onOpen={setEngineeringArtifact} />}
          {live && !activeSession.engineeringMode && activePlan && <PlanPanel plan={activePlan} />}
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
      {engineeringArtifact && activeEngineering && <EngineeringArtifactDialog engineering={activeEngineering} initialPhase={engineeringArtifact} onClose={() => setEngineeringArtifact(null)} />}
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

function LiveTimeline({ events, running, onAnswerQuestion }: { events: AgentEvent[]; running: boolean; onAnswerQuestion: (question: EngineeringQuestion, option: EngineeringOption, answer?: string) => Promise<void> }) {
  const approvalDecisions = new Map(
    events
      .filter((event) => event.type === 'approval_decision' && event.approval_id)
      .map((event) => [event.approval_id as string, event])
  );
  const currentQuestionId = [...events].reverse().find((event) => event.type === 'engineering_state')?.engineering?.pending_question?.question_id;
  return <>{events.map((event, index) => {
    if (event.type === 'run_started') return null;
    if (event.type === 'user') return <article className="user-turn" key={index}><div className="avatar user-avatar">FY</div><div className="turn-body"><div className="turn-meta"><b>你</b><time>{event.timestamp}</time></div><p>{event.message}</p></div></article>;
    if (event.type === 'engineering_decision') return <div className="notice-row engineering-decision" key={index}>✓ 你已提交工程决策：{event.message}</div>;
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
    if (event.type === 'engineering_state') return null;
    if (event.type === 'engineering_question' && event.question) {
      const active = currentQuestionId === event.question.question_id;
      return <EngineeringQuestionCard key={index} question={event.question} active={active} running={running} onAnswer={onAnswerQuestion} />;
    }
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
  const detail = result.unchanged
    ? '内容没有变化，已跳过写入'
    : result.ignored
    ? '软件工程模式已忽略普通计划更新'
    : plan
    ? `${plan.completed}/${plan.total} 个步骤已完成`
    : result.duration_ms
    ? `${Math.round(Number(result.duration_ms))}ms`
    : result.error
      ? String(result.error)
      : result.exit_code !== undefined
        ? `exit ${result.exit_code}`
        : '';
  const actual = result.actual_evidence as { id?: string; tool?: string } | undefined;
  const candidates = Array.isArray(result.candidate_evidence) ? result.candidate_evidence as Array<{ id: string; tool: string; summary?: string; valid?: boolean; reason?: string }> : [];
  return <div className={`tool-result-row ${ok ? 'ok' : 'failed'}`}><span>{ok ? '✓' : '×'}</span><div><b>{ok ? '执行成功' : '执行失败'} · {event.tool}</b><small>{detail}</small>{actual && <small className="tool-actual-evidence">实际证据：{actual.id} · {actual.tool}</small>}{candidates.length > 0 && <div className="evidence-candidates"><b>可用候选证据</b>{candidates.map((candidate) => <small key={candidate.id} className={candidate.valid === false ? 'invalid' : ''}><code>{candidate.id}</code> · {candidate.tool}{candidate.summary ? ` · ${compact(candidate.summary, 90)}` : ''}{candidate.reason ? `（${candidate.reason}）` : ''}</small>)}</div>}{result.next_action && <small className="tool-guidance">建议：{String(result.next_action)}</small>}{output && <pre>{compact(output, 1600)}</pre>}</div></div>;
}

function EngineeringQuestionCard({ question, active, running, onAnswer }: { question: EngineeringQuestion; active: boolean; running: boolean; onAnswer: (question: EngineeringQuestion, option: EngineeringOption, answer?: string) => Promise<void> }) {
  const [selected, setSelected] = useState('');
  const [answer, setAnswer] = useState('');
  const selectedOption = question.options.find((option) => option.id === selected);
  return <article className={`engineering-question ${active ? 'active' : 'resolved'}`}>
    <p className="eyebrow">ENGINEERING DECISION</p><h3>{question.question}</h3><p>{question.reason}</p>
    {question.baseline_review && <EngineeringBaselineReviewPanel review={question.baseline_review} />}
    {question.design_review && <EngineeringDesignReviewPanel review={question.design_review} expanded={active} />}
    {question.review_summary && <EngineeringReviewSummary review={question.review_summary} />}
    {question.workspace_review && <div className="workspace-review"><b>当前已验收项目：{question.workspace_review.project_title}</b><small>{question.workspace_review.requirements} 项需求 · {question.workspace_review.workspace}</small><p>{question.workspace_review.warning}</p></div>}
    <div className="engineering-options">{question.options.map((option) => option.requires_input ? <button type="button" key={option.id} className={selected === option.id ? 'selected' : ''} disabled={!active || running} onClick={() => { setSelected(option.id); setAnswer(''); }}><b>{option.label}</b>{option.description && <small>{option.description}</small>}</button> : <button type="button" key={option.id} disabled={!active || running} onClick={() => onAnswer(question, option)}><b>{option.label}</b>{option.description && <small>{option.description}</small>}</button>)}</div>
    {active && selectedOption?.requires_input && <div className="engineering-free-text"><label htmlFor={`decision-${question.question_id}`}>请补充具体修改内容</label><textarea id={`decision-${question.question_id}`} value={answer} onChange={(event) => setAnswer(event.target.value)} placeholder={selectedOption.input_placeholder || '请描述要修改的需求、验收标准或决策'} rows={3} disabled={running} /><button type="button" disabled={running || !answer.trim()} onClick={() => onAnswer(question, selectedOption, answer)}>提交“{selectedOption.label}”并继续</button></div>}
    {!active && <small className="question-resolved">该决策已记录</small>}
  </article>;
}

function EngineeringReviewSummary({ review }: { review: EngineeringReview }) {
  const moduleValue = review.modules_total === undefined ? review.design_modules : `${review.modules_completed}/${review.modules_total}`;
  const traceWarnings = [
    ...(review.incomplete_modules || []).map((item) => `${item.id} 缺少 ${item.missing_requirement_ids.join('、')}`),
    ...((review.untracked_files?.length || 0) ? [`${review.untracked_files?.length} 个已修改文件未追踪`] : []),
    ...((review.invalid_module_links?.length || 0) ? [`${review.invalid_module_links?.length} 条模块映射无效`] : []),
  ];
  return <div className="engineering-review"><div><b>{review.requirements}</b><small>需求</small></div><div className={traceWarnings.length ? 'warning' : ''}><b>{moduleValue}</b><small>模块追踪</small></div><div><b>{review.verification_links}</b><small>验证证据</small></div><div className={review.stale_evidence ? 'warning' : ''}><b>{review.stale_evidence}</b><small>过期证据</small></div>{traceWarnings.length > 0 && <p className="warning"><b>追踪待完善</b>{traceWarnings.join('；')}</p>}<p><b>剩余风险</b>{review.residual_risk}</p></div>;
}

function EngineeringBaselineReviewPanel({ review }: { review: EngineeringBaselineReview }) {
  const actors = review.actors || [];
  const useCases = review.use_cases || [];
  return <section className="baseline-review" aria-label="待确认需求基线">
    <div className="baseline-review-heading"><b>需求基线明细</b><span>{review.requirements.length} 项需求 · {useCases.length} 个用例</span></div>
    <div className="baseline-requirements">{review.requirements.map((requirement) => <article className="baseline-requirement" key={requirement.id}>
      <div><span className={requirement.kind === 'functional' ? 'functional' : 'non-functional'}>{requirement.kind === 'functional' ? 'FR' : 'NFR'}</span><b>{requirement.id} · {requirement.title}</b></div>
      <p>{requirement.description}</p>
      <small>验收标准</small>
      <ol>{requirement.acceptance_criteria.map((criterion, index) => <li key={`${requirement.id}-${index}`}>{criterion}</li>)}</ol>
    </article>)}</div>
    <UseCaseModel actors={actors} useCases={useCases} relationships={review.use_case_relationships || []} requirements={review.requirements} legacyEmpty />
    <div className="baseline-assumptions"><b>默认决策与假设</b>{review.assumptions.length ? <ul>{review.assumptions.map((item, index) => <li key={index}>{item}</li>)}</ul> : <p>本次需求基线未记录额外默认决策。</p>}</div>
  </section>;
}

function EngineeringPanel({ engineering, onOpen }: { engineering: EngineeringState; onOpen: (phase: EngineeringArtifactPhase) => void }) {
  const linkedRequirements = new Set([
    ...engineering.implementation_links.map((item) => item.requirement_id),
    ...engineering.test_links.map((item) => item.requirement_id),
  ]).size;
  return <section className="inspector-section engineering-panel">
    <div className="panel-heading"><span>Yukai-SE 工程流程</span><b>{engineering.status === 'completed' ? 'DONE' : 'ACTIVE'}</b></div>
    <div className="active-skill"><span>SE</span><div><b>{engineering.active_skill?.title || '软件工程 Skill'}</b><small>{engineering.active_skill?.description}</small></div></div>
    <div className="engineering-phases">{engineering.phases.map((phase) => <button type="button" className={`engineering-phase ${phase.status}`} key={phase.id} onClick={() => onOpen(phase.id as EngineeringArtifactPhase)}><span>{phase.status === 'completed' ? '✓' : phase.status === 'active' || phase.status === 'awaiting_user' ? '●' : '○'}</span><div><b>{phase.title}</b><small>{phase.gate.passed ? '质量门已满足 · 查看结果' : phase.gate.missing[0] || '等待前序阶段'}</small></div><em>›</em></button>)}</div>
    <div className="engineering-stats"><div><b>{engineering.requirements.length}</b><span>需求</span></div><div><b>{engineering.design_modules.length}</b><span>模块</span></div><div><b>{linkedRequirements}</b><span>追踪项</span></div></div>
    <small className="engineering-artifacts">产物保存在 <code>.yukai/engineering</code></small>
  </section>;
}

function EngineeringDesignReviewPanel({ review, expanded }: { review: EngineeringDesignReview; expanded: boolean }) {
  const uml = buildClassDiagram(review.uml_classes, review.uml_relationships);
  const processFlows = review.process_flows || [];
  const modules = <div className="design-review-modules">{review.modules.map((module) => <article key={module.id}><b>{module.id} · {module.name}</b><p>{module.responsibility}</p><small>需求：{module.requirement_ids.join('、')}</small></article>)}</div>;
  return <section className="design-review-summary" aria-label="待确认设计基线">
    <div><b>设计基线明细</b><span>{review.modules.length} 模块 · {review.uml_classes.length} 类 · {review.sequences.length} 时序 · {processFlows.length} 业务流程 · {review.domain_objects.length} 领域对象</span></div>
    {expanded ? <div className="design-review-content">
      <section><h4>UML 类图</h4>{uml ? <MermaidDiagram chart={uml} /> : <ArtifactEmpty text="尚未生成 UML 类图" />}</section>
      <section><h4>关键时序图</h4>{review.sequences.map((sequence) => <article className="design-review-sequence" key={sequence.id}><b>{sequence.id} · {sequence.name}</b><MermaidDiagram chart={buildSequenceDiagram(sequence)} /></article>)}</section>
      <section><h4>系统业务流程图</h4>{processFlows.length ? processFlows.map((flow) => <article className="design-review-sequence" key={flow.id}><b>{flow.id} · {flow.name}</b><MermaidDiagram chart={buildProcessFlowDiagram(flow)} /></article>) : <ArtifactEmpty text="旧设计基线未包含系统业务流程图" />}</section>
      <section><h4>领域模型</h4><div className="design-review-domains">{review.domain_objects.map((item) => <article key={item.id}><b>{domainKindLabel(item.kind)} · {item.name}</b><p>{item.description}</p><small>{item.business_rules.join('；')}</small></article>)}</div></section>
      <section><h4>设计模块</h4>{modules}</section>
    </div> : modules}
  </section>;
}

function EngineeringArtifactDialog({ engineering, initialPhase, onClose }: { engineering: EngineeringState; initialPhase: EngineeringArtifactPhase; onClose: () => void }) {
  const [phase, setPhase] = useState<EngineeringArtifactPhase>(initialPhase);
  const labels: Record<EngineeringArtifactPhase, string> = { requirements: '需求分析', design: '结构化设计', implementation: '结构化实现', verification: '测试验证', acceptance: '验收交付' };
  return <div className="modal-backdrop"><section className="engineering-artifact-dialog" role="dialog" aria-modal="true" aria-labelledby="engineering-artifact-title">
    <header><div><p className="eyebrow">YUKAI-SE ARTIFACT</p><h2 id="engineering-artifact-title">{engineering.project_title || '软件工程项目'}</h2></div><button className="dialog-close" type="button" onClick={onClose} aria-label="关闭工程产物">×</button></header>
    <nav aria-label="工程阶段">{engineering.phases.map((item) => <button type="button" key={item.id} className={phase === item.id ? 'active' : ''} onClick={() => setPhase(item.id as EngineeringArtifactPhase)}><span>{item.status === 'completed' ? '✓' : item.status === 'active' || item.status === 'awaiting_user' ? '●' : '○'}</span>{labels[item.id as EngineeringArtifactPhase]}</button>)}</nav>
    <div className="engineering-artifact-body">
      {phase === 'requirements' && <RequirementsArtifact engineering={engineering} />}
      {phase === 'design' && <DesignArtifact engineering={engineering} />}
      {phase === 'implementation' && <ImplementationArtifact engineering={engineering} />}
      {phase === 'verification' && <VerificationArtifact engineering={engineering} />}
      {phase === 'acceptance' && <AcceptanceArtifact engineering={engineering} />}
    </div>
  </section></div>;
}

function ArtifactHeading({ eyebrow, title, detail }: { eyebrow: string; title: string; detail: string }) {
  return <div className="artifact-heading"><div><p className="eyebrow">{eyebrow}</p><h3>{title}</h3></div><span>{detail}</span></div>;
}

function RequirementsArtifact({ engineering }: { engineering: EngineeringState }) {
  return <><ArtifactHeading eyebrow="REQUIREMENTS BASELINE" title="FR / NFR 与验收标准" detail={`${engineering.requirements.length} 项需求`} />
    <div className="artifact-requirements">{engineering.requirements.map((requirement) => {
      const verified = new Set(engineering.test_links.filter((link) => link.requirement_id === requirement.id).flatMap((link) => link.criterion_indices || []));
      return <article key={requirement.id}><header><span className={requirement.kind === 'functional' ? 'functional' : 'non-functional'}>{requirement.kind === 'functional' ? 'FR' : 'NFR'}</span><b>{requirement.id} · {requirement.title}</b><em>{verified.size}/{requirement.acceptance_criteria.length} 已验证</em></header><p>{requirement.description}</p><ol>{requirement.acceptance_criteria.map((criterion, index) => <li className={verified.has(index + 1) ? 'verified' : ''} key={index}><span>{verified.has(index + 1) ? '✓' : '○'}</span>{criterion}</li>)}</ol></article>;
    })}</div>
    <UseCaseModel actors={engineering.actors || []} useCases={engineering.use_cases || []} relationships={engineering.use_case_relationships || []} requirements={engineering.requirements} legacyEmpty />
  </>;
}

function UseCaseModel({ actors, useCases, relationships, requirements, legacyEmpty = false }: { actors: UseCaseActor[]; useCases: EngineeringUseCase[]; relationships: UseCaseRelationship[]; requirements: EngineeringRequirement[]; legacyEmpty?: boolean }) {
  const [selected, setSelected] = useState(useCases[0]?.id || '');
  const markerId = `use-case-arrow-${useId().replace(/:/g, '')}`;
  const generalizationMarkerId = `${markerId}-generalization`;
  if (!actors.length || !useCases.length) return <section className="artifact-section use-case-section"><h4>用例模型</h4><ArtifactEmpty text={legacyEmpty ? '旧需求基线未包含用例模型；重新进入需求分析并建立新基线后可生成' : '尚未生成用例模型'} /></section>;
  const rows = Math.ceil(useCases.length / 2);
  const height = Math.max(430, rows * 128 + 120, actors.length * 145 + 90);
  const actorPositions = new Map(actors.map((actor, index) => [actor.id, { x: 105, y: 105 + index * Math.max(120, (height - 170) / Math.max(1, actors.length - 1)) }]));
  const useCasePositions = new Map(useCases.map((useCase, index) => [useCase.id, { x: 490 + (index % 2) * 340, y: 120 + Math.floor(index / 2) * 128 }]));
  const selectedUseCase = useCases.find((item) => item.id === selected) || useCases[0];
  const functionalCriteria = requirements.filter((item) => item.kind === 'functional').reduce((sum, item) => sum + item.acceptance_criteria.length, 0);
  const coveredCriteria = new Set(useCases.flatMap((item) => item.acceptance_links.flatMap((link) => link.criterion_indices.map((index) => `${link.requirement_id}:${index}`)))).size;
  return <section className="artifact-section use-case-section"><div className="use-case-heading"><div><h4>UML 用例模型</h4><p>点击用例节点可查看完整规约；虚线箭头表示 include / extend 关系。</p></div><div><span>{actors.length} 参与者</span><span>{useCases.length} 用例</span><span>{coveredCriteria}/{functionalCriteria} FR 验收标准</span></div></div>
    <div className="use-case-diagram-scroll"><svg className="use-case-diagram" viewBox={`0 0 1060 ${height}`} role="img" aria-label="系统 UML 用例图">
      <defs><marker className="use-case-dependency-marker" id={markerId} markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto"><path d="M0,0 L9,4 L0,8" /></marker><marker className="use-case-generalization-marker" id={generalizationMarkerId} markerWidth="11" markerHeight="9" refX="10" refY="4.5" orient="auto"><path d="M0,0 L10,4.5 L0,9 z" /></marker></defs>
      <rect className="use-case-system-boundary" x="255" y="35" width="770" height={height - 70} rx="12" />
      <text className="use-case-system-title" x="278" y="66">{truncateLabel('系统用例边界', 18)}</text>
      {useCases.flatMap((useCase) => useCase.actor_ids.map((actorId) => { const actor = actorPositions.get(actorId); const target = useCasePositions.get(useCase.id); return actor && target ? <line className="use-case-association" key={`${actorId}-${useCase.id}`} x1={actor.x + 28} y1={actor.y} x2={target.x - 142} y2={target.y} /> : null; }))}
      {relationships.map((relationship, index) => { const source = useCasePositions.get(relationship.from); const target = useCasePositions.get(relationship.to); if (!source || !target) return null; const points = ellipseConnection(source, target); const stereotype = relationship.type === 'generalization' ? relationship.label || '泛化' : `«${relationship.type}»${relationship.label ? ` ${relationship.label}` : ''}`; return <g className={`use-case-relation ${relationship.type}`} key={`${relationship.from}-${relationship.to}-${index}`}><line x1={points.x1} y1={points.y1} x2={points.x2} y2={points.y2} markerEnd={`url(#${relationship.type === 'generalization' ? generalizationMarkerId : markerId})`} /><text x={(source.x + target.x) / 2} y={(source.y + target.y) / 2 - 8}>{stereotype}</text></g>; })}
      {actors.map((actor) => { const point = actorPositions.get(actor.id)!; return <g className="use-case-actor" key={actor.id}><circle cx={point.x} cy={point.y - 31} r="12" /><line x1={point.x} y1={point.y - 19} x2={point.x} y2={point.y + 19} /><line x1={point.x - 22} y1={point.y - 3} x2={point.x + 22} y2={point.y - 3} /><line x1={point.x} y1={point.y + 19} x2={point.x - 18} y2={point.y + 46} /><line x1={point.x} y1={point.y + 19} x2={point.x + 18} y2={point.y + 46} /><text x={point.x} y={point.y + 70}>{truncateLabel(actor.name, 12)}</text><title>{actor.id} · {actor.name}：{actor.description}</title></g>; })}
      {useCases.map((useCase) => { const point = useCasePositions.get(useCase.id)!; const active = selectedUseCase.id === useCase.id; return <g className={`use-case-node ${active ? 'selected' : ''}`} role="button" tabIndex={0} aria-label={`查看 ${useCase.id} ${useCase.name}`} key={useCase.id} onClick={() => setSelected(useCase.id)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelected(useCase.id); } }}><ellipse cx={point.x} cy={point.y} rx="138" ry="42" /><text x={point.x} y={point.y - 2}>{truncateLabel(useCase.name, 18)}</text><text className="use-case-id" x={point.x} y={point.y + 20}>{useCase.id}</text><title>{useCase.goal}</title></g>; })}
    </svg></div>
    <div className="use-case-legend"><span><i className="actor" />参与者关联</span><span><i className="include" />«include» 必须复用</span><span><i className="extend" />«extend» 条件扩展</span><span><i className="generalization" />泛化</span></div>
    <UseCaseSpecification useCase={selectedUseCase} actors={actors} requirements={requirements} />
  </section>;
}

function UseCaseSpecification({ useCase, actors, requirements }: { useCase: EngineeringUseCase; actors: UseCaseActor[]; requirements: EngineeringRequirement[] }) {
  const actorNames = useCase.actor_ids.map((id) => actors.find((actor) => actor.id === id)?.name || id);
  return <article className="use-case-specification"><header><div><span>USE CASE SPECIFICATION</span><h5>{useCase.id} · {useCase.name}</h5></div><em>{actorNames.join('、')}</em></header><p className="use-case-goal"><b>目标</b>{useCase.goal}</p><div className="use-case-condition-grid"><section><b>前置条件</b>{useCase.preconditions.length ? <ul>{useCase.preconditions.map((item, index) => <li key={index}>{item}</li>)}</ul> : <p>无</p>}</section><section><b>后置条件</b><ul>{useCase.postconditions.map((item, index) => <li key={index}>{item}</li>)}</ul></section></div><section className="use-case-main-flow"><b>主成功场景</b><ol>{useCase.main_flow.map((step, index) => <li key={index}><span>{index + 1}</span><p>{step}</p></li>)}</ol></section><section className="use-case-alternatives"><b>备选 / 异常流程</b>{useCase.alternative_flows.length ? useCase.alternative_flows.map((flow) => <details key={flow.id}><summary><span>{flow.id}</span><b>{flow.condition}</b></summary><ol>{flow.steps.map((step, index) => <li key={index}>{step}</li>)}</ol></details>) : <p>没有单独定义备选流程。</p>}</section><section className="use-case-trace"><b>需求与验收标准追踪</b>{useCase.acceptance_links.map((link) => { const requirement = requirements.find((item) => item.id === link.requirement_id); return <article key={link.requirement_id}><strong>{link.requirement_id} · {requirement?.title || '未找到需求'}</strong><ul>{link.criterion_indices.map((index) => <li key={index}><span>AC-{index}</span>{requirement?.acceptance_criteria[index - 1] || `验收标准 ${index}`}</li>)}</ul></article>; })}</section></article>;
}

function truncateLabel(value: string, length: number) { return value.length > length ? `${value.slice(0, length - 1)}…` : value; }

function ellipseConnection(source: { x: number; y: number }, target: { x: number; y: number }) {
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const boundaryScale = 1 / Math.sqrt((dx * dx) / (138 * 138) + (dy * dy) / (42 * 42));
  return { x1: source.x + dx * boundaryScale, y1: source.y + dy * boundaryScale, x2: target.x - dx * boundaryScale, y2: target.y - dy * boundaryScale };
}

function DesignArtifact({ engineering }: { engineering: EngineeringState }) {
  const uml = buildClassDiagram(engineering.uml_classes || [], engineering.uml_relationships || []);
  return <><ArtifactHeading eyebrow="DESIGN BASELINE" title="UML、时序图、业务流程与领域模型" detail={`${engineering.design_modules.length} 模块`} />
    <section className="artifact-section"><h4>UML 类图</h4>{uml ? <MermaidDiagram chart={uml} /> : <ArtifactEmpty text="尚未生成 UML 类图" />}</section>
    <section className="artifact-section"><h4>关键业务时序图</h4>{engineering.sequences?.length ? <div className="sequence-grid">{engineering.sequences.map((sequence) => <article key={sequence.id}><div><b>{sequence.id} · {sequence.name}</b><small>{sequence.requirement_ids.join('、')}</small></div><MermaidDiagram chart={buildSequenceDiagram(sequence)} /></article>)}</div> : <ArtifactEmpty text="尚未生成业务时序图" />}</section>
    <section className="artifact-section"><h4>系统业务流程图</h4>{engineering.process_flows?.length ? <div className="sequence-grid process-flow-grid">{engineering.process_flows.map((flow) => <article key={flow.id}><div><b>{flow.id} · {flow.name}</b><small>覆盖需求：{flow.requirement_ids.join('、')}</small></div><MermaidDiagram chart={buildProcessFlowDiagram(flow)} /></article>)}</div> : <ArtifactEmpty text="当前设计基线尚未包含系统业务流程图；重新进入设计阶段后可生成" />}</section>
    <section className="artifact-section"><h4>领域模型</h4>{engineering.domain_objects?.length ? <div className="domain-model-grid">{engineering.domain_objects.map((item) => <article key={item.id} className={`domain-${item.kind}`}><header><span>{domainKindLabel(item.kind)}</span><b>{item.name}</b></header><p>{item.description}</p>{item.business_rules.length > 0 && <ul>{item.business_rules.map((rule, index) => <li key={index}>{rule}</li>)}</ul>}<small>{item.requirement_ids.join('、')}</small></article>)}</div> : <ArtifactEmpty text="尚未生成领域模型" />}</section>
    <section className="artifact-section"><h4>设计模块</h4><div className="design-module-grid">{engineering.design_modules.map((module) => <article key={module.id}><header><b>{module.id} · {module.name}</b><span>{module.requirement_ids.length} 需求</span></header><p>{module.responsibility}</p>{module.interfaces.length > 0 && <small>接口：{module.interfaces.join('；')}</small>}{module.dependencies?.length ? <small>依赖：{module.dependencies.join('、')}</small> : null}</article>)}</div></section>
  </>;
}

function ImplementationArtifact({ engineering }: { engineering: EngineeringState }) {
  const audit = engineering.implementation_audit;
  const completed = audit?.modules_completed ?? engineering.design_modules.filter((module) => moduleImplementationStatus(engineering, module).complete).length;
  const percent = engineering.design_modules.length ? Math.round(completed / engineering.design_modules.length * 100) : 0;
  const covered = new Set(engineering.implementation_links.map((link) => link.requirement_id)).size;
  return <><ArtifactHeading eyebrow="IMPLEMENTATION TRACE" title="模块—文件映射与实现进度" detail={`${completed}/${engineering.design_modules.length} 模块`} />
    <div className="artifact-progress"><div><b>{percent}%</b><span>模块实现追踪完整率</span></div><div className="artifact-progress-track"><i style={{ width: `${percent}%` }} /></div><small>需求实现覆盖 {covered}/{engineering.requirements.length}</small></div>
    <div className="implementation-map">{engineering.design_modules.map((module) => { const audited = audit?.modules.find((item) => item.id === module.id); const paths = audited?.paths || implementationPaths(engineering, module); const fallback = moduleImplementationStatus(engineering, module); const status = audited ? { complete: audited.complete, covered: audited.covered_requirement_ids.length, total: audited.required_requirement_ids.length, missing: audited.missing_requirement_ids } : { ...fallback, missing: module.requirement_ids.filter((item) => !engineering.implementation_links.some((link) => link.requirement_id === item && link.module_ids?.includes(module.id))) }; const state = status.complete ? 'implemented' : status.covered > 0 ? 'partial' : ''; return <article key={module.id} className={state}><header><span>{status.complete ? '✓' : status.covered ? '◐' : '○'}</span><b>{module.id} · {module.name}</b><em>{status.complete ? '已完成追踪' : status.covered ? `进行中 ${status.covered}/${status.total}` : '待追踪'}</em></header><p>{module.responsibility}</p><div>{paths.length ? paths.map((path) => <code key={path}>{path}</code>) : <small>尚未绑定文件修改证据</small>}</div>{status.missing.length > 0 && <div className="module-missing"><b>缺少需求映射</b><span>{status.missing.join('、')}</span></div>}<footer>{module.requirement_ids.join('、')}</footer></article>; })}</div>
    {audit && (audit.untracked_files.length > 0 || audit.invalid_module_links.length > 0) && <section className="traceability-alert"><h4>实现追踪待完善</h4>{audit.untracked_files.length > 0 && <div><b>未追踪的已修改文件</b>{audit.untracked_files.map((path) => <code key={path}>{path}</code>)}</div>}{audit.invalid_module_links.length > 0 && <div><b>无效模块映射</b>{audit.invalid_module_links.map((item, index) => <code key={`${item.requirement_id}-${item.module_id}-${index}`}>{item.requirement_id} → {item.module_id} · {item.path}</code>)}</div>}</section>}
  </>;
}

function VerificationArtifact({ engineering }: { engineering: EngineeringState }) {
  const [tab, setTab] = useState<'overview' | 'black_box' | 'white_box' | 'coverage' | 'supporting'>('overview');
  const summary = engineering.verification_summary;
  const run = summary?.latest_run;
  const blackCases = run?.cases.filter((item) => item.method === 'black_box') || [];
  const whiteCases = run?.cases.filter((item) => item.method === 'white_box') || [];
  const supportingCases = run?.cases.filter((item) => item.method === 'supporting') || [];
  const supporting = summary?.supporting_items || [];
  const tabs = [
    { id: 'overview', label: '测试总览', count: run?.total },
    { id: 'black_box', label: '黑盒测试', count: run?.black_box.total },
    { id: 'white_box', label: '白盒测试', count: run?.white_box.total },
    { id: 'coverage', label: '需求覆盖', count: engineering.requirements.length },
    { id: 'supporting', label: '辅助检查', count: supportingCases.length || summary?.supporting_checks },
  ] as const;
  return <><ArtifactHeading eyebrow="VERIFICATION EVIDENCE" title="真实测试用例与需求覆盖" detail={run ? `${run.passed}/${run.total} 通过` : '尚无结构化测试运行'} />
    {run ? <>
      <section className={`test-run-banner ${run.status}`}><div><span>{run.status === 'passed' ? '✓' : '!'}</span><div><b>{run.status === 'passed' ? '本次测试运行通过' : '本次测试运行存在失败'}</b><small>{run.framework} · 退出码 {run.exit_code ?? '未记录'} · {run.duration_seconds == null ? '耗时未记录' : `耗时 ${run.duration_seconds.toFixed(3)} 秒`}</small></div></div><code>{run.command}</code></section>
      <div className="test-result-stats"><div><b>{run.total}</b><span>真实测试用例</span></div><div className="passed"><b>{run.passed}</b><span>通过</span></div><div className={run.failed ? 'failed' : ''}><b>{run.failed}</b><span>失败</span></div><div className={run.errors ? 'failed' : ''}><b>{run.errors}</b><span>错误</span></div><div><b>{run.skipped}</b><span>跳过</span></div></div>
      <nav className="test-artifact-tabs" aria-label="测试结果分类">{tabs.map((item) => <button type="button" key={item.id} className={tab === item.id ? 'active' : ''} onClick={() => setTab(item.id)}>{item.label}<span>{item.count ?? 0}</span></button>)}</nav>
      {tab === 'overview' && <TestOverview run={run} summary={summary} audit={engineering.test_strategy_audit} />}
      {tab === 'black_box' && <TestCaseGroups cases={blackCases} requirements={engineering.requirements} empty="没有识别到黑盒测试用例" />}
      {tab === 'white_box' && <TestCaseGroups cases={whiteCases} requirements={engineering.requirements} empty="没有识别到白盒测试用例" />}
      {tab === 'coverage' && <RequirementTestCoverage engineering={engineering} cases={run.cases} />}
      {tab === 'supporting' && <SupportingChecks items={supporting} cases={supportingCases} requirements={engineering.requirements} />}
    </> : <><div className="traceability-alert"><h4>尚未记录真实测试用例</h4><p>当前只有需求证据关系，重新运行详细测试后才能显示真实用例数量与结果。</p></div><TestEvidenceGroup title="现有验证证据" links={engineering.test_links} /></>}
  </>;
}

function TestEvidenceGroup({ title, links }: { title: string; links: EngineeringState['test_links'] }) {
  return <section className="artifact-section test-evidence-section"><h4>{title}</h4>{links.length ? <div>{links.map((link, index) => <article key={`${link.requirement_id}-${link.command}-${index}`}><header><b>{link.requirement_id}</b><span>{testLevelLabel(link.test_level)}</span><em>✓ 通过</em></header><p>{link.claim || '已绑定成功验证证据'}</p><code>{link.command}</code><small>验收标准：{link.criterion_indices?.join('、') || '未记录'}</small></article>)}</div> : <ArtifactEmpty text={`暂无${title}证据`} />}</section>;
}

function TestOverview({ run, summary, audit }: { run: VerificationRun; summary?: VerificationSummary; audit?: TestStrategyAudit }) {
  const strategyReady = audit ? audit.passed : false;
  return <div className="test-overview"><div className="test-method-stats"><TestMethodCard label="BLACK BOX" title="黑盒测试" stats={run.black_box} detail="从系统外部验证公开接口、输入、输出与错误行为" /><TestMethodCard label="WHITE BOX" title="白盒测试" stats={run.white_box} detail="直接验证内部模块、分支、状态转换和数据不变量" /><div><span>SUPPORTING</span><b>{summary?.supporting_checks || 0}</b><small>辅助检查，不计入黑白盒用例</small></div>{run.unclassified.total > 0 && <TestMethodCard label="UNCLASSIFIED" title="未分类" stats={run.unclassified} detail="需要在测试文件或类中声明测试方法" />}</div>{audit && <section className={`test-strategy-audit ${strategyReady ? 'passed' : 'incomplete'}`}><header><div><b>黑白盒质量门</b><small>{audit.required ? '新工程强制约束' : '历史工程仅评估，不阻断验收'}</small></div><span>{strategyReady ? '✓ 已满足' : '! 待完善'}</span></header><div><article><strong>{audit.functional_criteria_black_box_covered}/{audit.functional_criteria_total}</strong><b>功能验收标准黑盒覆盖</b><small>每条功能验收标准至少由一个公开行为测试证明</small></article><article><strong>{audit.core_modules_white_box_covered}/{audit.core_modules_total}</strong><b>核心模块白盒覆盖</b><small>每个承载功能需求的设计模块至少有一个业务白盒用例</small></article></div>{audit.missing.length > 0 && <ul>{audit.missing.map((item) => <li key={item}>{item}</li>)}</ul>}</section>}<section className="test-scope-note"><b>测试分类边界</b><p>黑盒测试通过系统公开接口验证输入、输出和错误行为；白盒测试直接验证内部模块、分支、状态转换和数据一致性。静态分析、导入检查与依赖检查单独列为辅助证据。</p><small>{summary?.dynamic_trace_links || 0} 条动态测试证据关系 · {summary?.supporting_checks || 0} 项辅助检查</small></section></div>;
}

function TestMethodCard({ label, title, stats, detail }: { label: string; title: string; stats: TestMethodStats; detail: string }) {
  return <div className={stats.failed || stats.errors ? 'has-failure' : ''}><span>{label}</span><b>{stats.passed}/{stats.total}</b><strong>{title}</strong><small>{detail}</small></div>;
}

function TestCaseGroups({ cases, requirements, empty }: { cases: VerificationTestCase[]; requirements: EngineeringRequirement[]; empty: string }) {
  if (!cases.length) return <ArtifactEmpty text={empty} />;
  const groups = [...cases.reduce((map, item) => { const key = item.path || item.suite || '未识别测试文件'; const current = map.get(key) || []; current.push(item); map.set(key, current); return map; }, new Map<string, VerificationTestCase[]>())];
  return <div className="test-case-groups">{groups.map(([path, items]) => { const passed = items.filter((item) => item.status === 'passed').length; return <details key={path} open><summary><div><b>{path}</b><small>{[...new Set(items.map((item) => item.suite))].join(' · ')}</small></div><span className={passed === items.length ? 'passed' : 'failed'}>{passed}/{items.length} 通过</span></summary><div className="test-case-list">{items.map((item) => <TestCaseDetail key={item.id} item={item} requirements={requirements} />)}</div></details>; })}</div>;
}

function TestCaseDetail({ item, requirements }: { item: VerificationTestCase; requirements: EngineeringRequirement[] }) {
  return <details className={`test-case-detail ${item.status}`}><summary><span>{testStatusIcon(item.status)}</span><div><b>{item.name}</b><small>{item.purpose}</small></div><em>{testStatusLabel(item.status)}</em></summary><div><dl><dt>测试层级</dt><dd>{testLevelLabel(item.level)}</dd><dt>源文件</dt><dd><code>{item.path}{item.line ? `:${item.line}` : ''}</code></dd><dt>完整标识</dt><dd><code>{item.id}</code></dd></dl>{item.traces?.length ? <section><b>覆盖需求与验收标准</b>{item.traces.map((trace, index) => { const requirement = requirements.find((value) => value.id === trace.requirement_id); return <p key={`${trace.requirement_id}-${index}`}><strong>{trace.requirement_id} · {requirement?.title || '未找到需求标题'}</strong><span>{trace.criterion_indices.map((criterion) => requirement?.acceptance_criteria[criterion - 1] || `验收标准 ${criterion}`).join('；')}</span></p>; })}</section> : <p className="test-unlinked">该用例尚未单独绑定需求；不影响运行结果，但追踪信息仍可完善。</p>}{item.detail && <pre>{item.detail}</pre>}</div></details>;
}

function RequirementTestCoverage({ engineering, cases }: { engineering: EngineeringState; cases: VerificationTestCase[] }) {
  return <div className="requirement-test-coverage">{engineering.requirements.map((requirement) => { const links = engineering.test_links.filter((link) => link.requirement_id === requirement.id); const covered = new Set(links.flatMap((link) => link.criterion_indices || [])); const linkedCases = cases.filter((item) => item.traces?.some((trace) => trace.requirement_id === requirement.id)); return <details key={requirement.id}><summary><div><b>{requirement.id} · {requirement.title}</b><small>{linkedCases.length} 个明确关联用例 · {links.length} 条证据关系</small></div><span className={covered.size === requirement.acceptance_criteria.length ? 'passed' : 'failed'}>{covered.size}/{requirement.acceptance_criteria.length} 验收标准</span></summary><ol>{requirement.acceptance_criteria.map((criterion, index) => <li key={index} className={covered.has(index + 1) ? 'passed' : ''}><span>{covered.has(index + 1) ? '✓' : '○'}</span><div><b>{criterion}</b>{linkedCases.filter((item) => item.traces?.some((trace) => trace.requirement_id === requirement.id && trace.criterion_indices.includes(index + 1))).map((item) => <code key={item.id}>{item.name}</code>)}</div></li>)}</ol></details>; })}</div>;
}

function SupportingChecks({ items, cases, requirements }: { items: SupportingCheck[]; cases: VerificationTestCase[]; requirements: EngineeringRequirement[] }) {
  return <div className="supporting-checks"><section className="test-scope-note"><b>辅助检查不属于黑盒或白盒测试</b><p>这里展示依赖/结构合规测试、静态分析、文件检查和环境检查。它们可以证明约束，但不会增加业务黑盒或白盒用例数量。</p></section>{cases.length > 0 && <TestCaseGroups cases={cases} requirements={requirements} empty="暂无辅助测试用例" />}{items.length ? items.map((item, index) => <article key={`${item.requirement_id}-${index}`}><span>✓</span><div><header><b>{item.requirement_id}</b><em>{item.kind === 'inspection' ? '人工/静态检查' : item.kind === 'supporting_test' ? '辅助合规测试' : item.kind}</em></header><p>{item.claim}</p><code>{item.command}</code><small>验收标准：{item.criterion_indices.join('、')}</small></div></article>) : cases.length === 0 ? <ArtifactEmpty text="暂无辅助检查" /> : null}</div>;
}

function testStatusIcon(status: VerificationTestCase['status']) { return status === 'passed' ? '✓' : status === 'skipped' ? '−' : status === 'unknown' ? '?' : '×'; }
function testStatusLabel(status: VerificationTestCase['status']) { return ({ passed: '通过', failed: '失败', error: '错误', skipped: '跳过', unknown: '未记录' } as const)[status]; }

function AcceptanceArtifact({ engineering }: { engineering: EngineeringState }) {
  const audit = engineering.implementation_audit;
  const testRun = engineering.verification_summary?.latest_run;
  const requirementCovered = new Set(engineering.implementation_links.map((link) => link.requirement_id)).size;
  const criteriaTotal = engineering.requirements.reduce((sum, item) => sum + item.acceptance_criteria.length, 0);
  const criteriaCovered = engineering.requirements.reduce((sum, requirement) => sum + new Set(engineering.test_links.filter((link) => link.requirement_id === requirement.id).flatMap((link) => link.criterion_indices || [])).size, 0);
  const moduleCompleted = audit?.modules_completed ?? engineering.design_modules.filter((module) => moduleImplementationStatus(engineering, module).complete).length;
  const deliverables = [...new Set([...(audit?.changed_files || []), ...engineering.implementation_links.map((link) => link.path)])].sort();
  const acceptanceDecision = engineering.decisions?.find((decision) => decision.key === 'project_acceptance' && ['approve', 'accept', 'accepted', 'confirm', 'confirmed', 'yes'].includes(decision.option_id.toLowerCase()));
  const traceabilityComplete = audit?.passed ?? moduleCompleted === engineering.design_modules.length;
  const testStrategy = engineering.test_strategy_audit;
  const testStrategyReady = !testStrategy?.required || testStrategy.passed;
  const accepted = engineering.status === 'completed' && traceabilityComplete && testStrategyReady && requirementCovered === engineering.requirements.length && criteriaCovered === criteriaTotal ? acceptanceDecision : undefined;
  const risks = [
    ...(criteriaCovered < criteriaTotal ? [`仍有 ${criteriaTotal - criteriaCovered} 条验收标准缺少验证证据`] : []),
    ...(requirementCovered < engineering.requirements.length ? [`仍有 ${engineering.requirements.length - requirementCovered} 项需求缺少实现映射`] : []),
    ...((audit?.incomplete_modules || []).map((item) => `${item.id} 缺少需求映射：${item.missing_requirement_ids.join('、')}`)),
    ...((audit?.untracked_files.length || 0) ? [`${audit?.untracked_files.length} 个已修改文件未加入实现追踪`] : []),
    ...((audit?.invalid_module_links.length || 0) ? [`${audit?.invalid_module_links.length} 条需求—模块映射与设计基线不一致`] : []),
    ...((testStrategy?.required && !testStrategy.passed) ? testStrategy.missing : []),
    ...(engineering.test_links.some((link) => !link.test_method && !['static_analysis', 'inspection'].includes(link.evidence_kind || '')) ? ['存在旧版未分类测试证据'] : []),
    '验证结论只覆盖已确认的需求基线、当前实现和当前运行环境',
  ];
  return <><ArtifactHeading eyebrow="ACCEPTANCE DELIVERY" title="完成率、风险与交付结论" detail={accepted ? '已验收' : '等待验收'} />
    <div className="acceptance-metrics"><Metric label="需求实现" value={requirementCovered} total={engineering.requirements.length} /><Metric label="模块实现追踪" value={moduleCompleted} total={engineering.design_modules.length} /><Metric label="验收标准" value={criteriaCovered} total={criteriaTotal} /><Metric label="测试用例通过" value={testRun?.passed || 0} total={testRun?.total || 1} /></div>
    <div className="acceptance-columns"><section><h4>剩余风险</h4><div className="risk-list">{risks.map((risk, index) => <article key={index}><span>{index === risks.length - 1 ? 'LOW' : 'MED'}</span><p>{risk}</p></article>)}</div></section><section><h4>交付文件</h4><div className="delivery-list">{deliverables.length ? deliverables.map((path) => <code key={path}>{path}</code>) : <ArtifactEmpty text="尚未绑定交付文件" />}</div></section></div>
    <div className={`acceptance-conclusion ${accepted ? 'accepted' : ''}`}><span>{accepted ? '✓' : acceptanceDecision ? '!' : '?'}</span><div><b>{accepted ? '项目已通过用户验收' : acceptanceDecision ? '历史验收结论已失效' : '等待用户验收结论'}</b><small>{accepted ? `${accepted.option_label} · ${accepted.decided_at}` : acceptanceDecision ? '新的一致性检查发现追踪缺口，修复后需重新验收' : '完成所有质量门后，由用户确认是否接收交付物'}</small></div></div>
  </>;
}

function Metric({ label, value, total }: { label: string; value: number; total: number }) {
  const percent = total ? Math.round(value / total * 100) : 0;
  return <div><b>{percent}%</b><span>{label}</span><small>{value}/{total}</small></div>;
}

function ArtifactEmpty({ text }: { text: string }) { return <div className="artifact-empty">{text}</div>; }

function MermaidDiagram({ chart }: { chart: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let cancelled = false;
    const container = ref.current;
    const render = async () => {
      try {
        const mermaidModule = await import('mermaid');
        mermaidModule.default.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'dark', fontFamily: 'ui-monospace, Consolas, monospace' });
        const id = `yukai-diagram-${Math.random().toString(36).slice(2)}`;
        const result = await mermaidModule.default.render(id, chart);
        if (!cancelled && container) container.innerHTML = result.svg;
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : '图表渲染失败');
      }
    };
    render();
    return () => { cancelled = true; if (container) container.innerHTML = ''; };
  }, [chart]);
  return error ? <div className="artifact-empty error">{error}</div> : <div className="mermaid-diagram" ref={ref} />;
}

function buildClassDiagram(classes: UmlClass[], relationships: UmlRelationship[]) {
  if (!classes.length) return '';
  const names = new Map(classes.map((item) => [item.id, mermaidId(`C_${item.id}`)]));
  const lines = ['classDiagram'];
  classes.forEach((item) => {
    const name = names.get(item.id) || mermaidId(item.id);
    lines.push(`class ${name}["${mermaidText(item.name)}"] {`);
    item.attributes.forEach((attribute) => lines.push(`  ${mermaidMember(attribute)}`));
    item.methods.forEach((method) => lines.push(`  ${mermaidMember(method)}`));
    lines.push('}');
  });
  const arrows: Record<UmlRelationship['type'], string> = { association: '-->', inheritance: '<|--', composition: '*--', aggregation: 'o--', dependency: '..>' };
  relationships.forEach((item) => lines.push(`${names.get(item.from) || mermaidId(item.from)} ${arrows[item.type]} ${names.get(item.to) || mermaidId(item.to)}${item.label ? ` : ${mermaidText(item.label)}` : ''}`));
  return lines.join('\n');
}

function buildSequenceDiagram(sequence: EngineeringSequence) {
  const aliases = new Map(sequence.participants.map((name, index) => [name, `P${index + 1}`]));
  const lines = ['sequenceDiagram', 'autonumber'];
  sequence.participants.forEach((name) => lines.push(`participant ${aliases.get(name)} as ${mermaidText(name)}`));
  sequence.steps.forEach((step) => lines.push(`${aliases.get(step.from)}${step.response ? '-->>' : '->>'}${aliases.get(step.to)}: ${mermaidText(step.message)}`));
  return lines.join('\n');
}

function buildProcessFlowDiagram(flow: ProcessFlow) {
  const aliases = new Map(flow.nodes.map((node) => [node.id, mermaidId(`F_${flow.id}_${node.id}`)]));
  const lines = [`flowchart ${flow.direction || 'TD'}`];
  flow.nodes.forEach((node) => {
    const id = aliases.get(node.id) || mermaidId(node.id);
    const label = mermaidText(node.label);
    if (node.type === 'start' || node.type === 'end') lines.push(`${id}(["${label}"])`);
    else if (node.type === 'decision') lines.push(`${id}{"${label}"}`);
    else if (node.type === 'input_output') lines.push(`${id}[/"${label}"/]`);
    else lines.push(`${id}["${label}"]`);
  });
  flow.edges.forEach((edge) => {
    const source = aliases.get(edge.from) || mermaidId(edge.from);
    const target = aliases.get(edge.to) || mermaidId(edge.to);
    lines.push(edge.label ? `${source} -->|${mermaidText(edge.label)}| ${target}` : `${source} --> ${target}`);
  });
  return lines.join('\n');
}

function implementationPaths(engineering: EngineeringState, module: EngineeringModule) {
  return [...new Set(engineering.implementation_links.filter((link) => link.module_ids?.includes(module.id) || (!link.module_ids && module.requirement_ids.includes(link.requirement_id))).map((link) => link.path))];
}

function moduleImplementationStatus(engineering: EngineeringState, module: EngineeringModule) {
  const requirementIds = new Set(engineering.implementation_links.filter((link) => link.module_ids?.includes(module.id) || (!link.module_ids && module.requirement_ids.includes(link.requirement_id))).map((link) => link.requirement_id));
  const covered = module.requirement_ids.filter((requirementId) => requirementIds.has(requirementId)).length;
  return { covered, total: module.requirement_ids.length, complete: module.requirement_ids.length > 0 && covered === module.requirement_ids.length && implementationPaths(engineering, module).length > 0 };
}

function mermaidId(value: string) { const normalized = value.replace(/[^A-Za-z0-9_\u4e00-\u9fff]/g, '_'); return normalized || 'Unnamed'; }
function mermaidText(value: string) { return value.replace(/[\n\r:;{}\[\]"]/g, ' ').trim(); }
function mermaidMember(value: string) { return mermaidText(value).replace(/[<>]/g, ''); }
function domainKindLabel(kind: DomainObject['kind']) { return ({ aggregate_root: '聚合根', entity: '实体', value_object: '值对象', domain_service: '领域服务', repository: '仓储' } as const)[kind]; }
function testLevelLabel(level?: string) { return ({ unit: '单元', integration: '集成', system: '系统', acceptance: '验收', performance: '性能', security: '安全', static: '静态' } as Record<string, string>)[level || ''] || '未分级'; }

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
  const changes = events.filter((event) => ['write_file', 'edit_file'].includes(event.tool || '') && event.type === 'tool_result' && event.result?.ok && !event.result?.unchanged && event.result?.snapshot_id);
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

function toolIcon(tool?: string) { return ({ list_files: '⌕', read_file: '≡', search_text: '⌕', write_file: '+', edit_file: '±', make_directory: '□', run_command: '›_', update_plan: '✓', update_engineering_state: 'SE', request_user_input: '?' } as Record<string, string>)[tool || ''] || '◆'; }
function toolLabel(event: AgentEvent) { const args = event.arguments || {}; const path = String(args.path || '.'); return ({ list_files: `浏览 ${path}`, read_file: `读取 ${path}`, search_text: `搜索 “${args.query || ''}”`, write_file: `写入 ${path}`, edit_file: `编辑 ${path}`, make_directory: `创建目录 ${path}`, run_command: `运行 ${compact(String(args.command || ''), 90)}`, update_plan: `更新计划 · ${compact(String(args.summary || '当前任务'), 60)}`, update_engineering_state: `更新工程流程 · ${String(args.action || '')}`, request_user_input: `请求工程决策 · ${compact(String(args.question || ''), 60)}` } as Record<string, string>)[event.tool || ''] || String(event.tool || 'Agent 操作'); }
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
    engineeringMode: false,
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
function finalStatus(reason?: string) { if (reason === 'completed') return { icon: '✓', title: '完成', meta: '任务完成' }; if (reason === 'awaiting_user') return { icon: '?', title: '等待你的工程决策', meta: '等待确认' }; if (reason === 'checkpoint') return { icon: '↻', title: '到达工程检查点', meta: '可继续' }; if (reason === 'blocked') return { icon: '!', title: '任务被阻塞', meta: '需要处理' }; if (reason === 'incomplete_plan') return { icon: '!', title: '计划未完成', meta: '未完成' }; return { icon: '■', title: '任务已停止', meta: '已停止' }; }

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
