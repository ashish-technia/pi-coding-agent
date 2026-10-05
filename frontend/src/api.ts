// Typed client for the pi-jira-agent API. Shapes mirror src/pi_jira_agent/service.py.

export type Status =
  | 'not_started'
  | 'fetching'
  | 'framing_requirements'
  | 'pending_requirements'
  | 'planning'
  | 'pending_plan'
  | 'coding'
  | 'reviewing'
  | 'pending_phase'
  | 'pending_final'
  | 'creating_pr'
  | 'done'
  | 'failed'
  | 'cancelled'
  | 'stuck_error'

export type Stage = 'requirements' | 'plan' | 'code' | 'review' | 'final' | 'pr'

export interface JiraComment {
  id: string
  author: string
  created: string
  body: string
}

export interface IssueDetails {
  key: string
  summary: string
  description: string
  project_key: string
  reporter: string | null
  issue_type: string
  status: string
  labels: string[]
  comments: JiraComment[]
  url: string
}

export interface Requirements {
  title: string
  problem: string
  goals: string[]
  acceptance_criteria: string[]
  in_scope: string[]
  out_of_scope: string[]
  assumptions: string[]
  open_questions: string[]
  sources: string[]
}

export interface ScopeFinding {
  item: string
  kind: 'added' | 'removed' | 'changed'
  verdict: 'in_scope' | 'out_of_scope' | 'unclear'
  reason: string
}

export interface ScopeCheck {
  findings: ScopeFinding[]
}

export interface PlanStep {
  file: string
  action: 'modify' | 'create' | 'delete'
  change: string
  evidence: string
  /** Which repo `file` lives in. Empty on single-repo runs. */
  repo: string
}

export interface RepoConfig {
  name: string
  path: string
  bitbucket_repo_slug: string
  target_branch: string
  default_selected: boolean
  properties: Record<string, string>
}

export interface PlanPhase {
  name: string
  description: string
  step_indexes: number[]
}

export interface AgentResult {
  branch_name: string
  commit_message: string
  pr_title: string
  pr_description: string
  files_changed: string[]
  analysis: string
  plan_steps: PlanStep[]
  verification: string[]
  open_questions: string[]
  notes_response: string
  phases: PlanPhase[]
}

/** Every pending gate carries `gate_id`, unique to this pause; a decision must echo it. */
export type Pending = { gate_id: string } & (
  | { type: 'requirements_approval'; issue_key: string; requirements: Requirements; scope_check: ScopeCheck | null }
  | { type: 'plan_approval'; issue_key: string; plan: AgentResult; phases_total: number }
  | {
      type: 'phase_gate'
      issue_key: string
      phase_index: number
      phases_total: number
      files_changed: string[]
      /** Working-tree diff per repo; repos with no changes are absent. */
      diffs: Record<string, string>
      /** What this phase alone changed, per repo: the part the review just judged. */
      phase_diff?: Record<string, string>
      review_feedback: string
      /** Changed files the review did not see because the diff was over the size limit. */
      review_omitted_files?: string[]
    }
  | {
      type: 'final_review'
      issue_key: string
      diffs: Record<string, string>
      diff_lines: number
      files_changed: string[]
      review_feedback: string
      review_omitted_files?: string[]
      pr_enabled: boolean
      suggested_pr_title: string
      suggested_pr_description: string
      phases_completed: number
      phases_total: number
    }
)

export interface ActivityEvent {
  seq: number
  at: number
  source?: 'pi' | 'llm'
  ev: 'node' | 'start' | 'model_turn' | 'assistant' | 'tool' | 'tool_done' | 'validation' | 'done' | 'error' | 'llm_call' | 'llm_done'
  t?: number
  node?: string
  label?: string
  mode?: string
  model?: string
  thinking?: string
  tools?: string
  turn?: number
  text?: string
  chars?: number
  tool?: string
  args?: string
  id?: string
  error?: boolean
  size?: number
  round?: number
  problems?: string[]
  files_read?: number
  plan_steps?: number
  corrections?: number
  stage?: string
}

export interface RunStatus {
  issue: string
  issue_details: IssueDetails | null
  channel: 'ui' | 'jira'
  running: boolean
  node: string | null
  node_label: string | null
  node_elapsed_s: number | null
  pi_timeout_s: number | null
  activity: ActivityEvent[]
  stage: Stage | null
  status: Status
  pending: Pending | null
  requirements: Requirements | null
  requirements_original: Requirements | null
  scope_check: ScopeCheck | null
  plan_result: AgentResult | null
  execution_mode: 'all' | 'phased' | null
  phase_index: number | null
  phases_total: number | null
  iteration: number | null
  max_iterations: number | null
  retry_count: number
  code_result: AgentResult | null
  /** Repo names this run works in, primary first. */
  repos: string[]
  diffs: Record<string, string>
  review_approved: boolean | null
  review_feedback: string | null
  pr_title: string | null
  pr_urls: Record<string, string>
  error?: string
  stuck_on?: string[]
}

export interface RunSummary {
  issue_key: string
  summary: string
  status: string
  channel: string
  created_at: string
  updated_at: string
  running: boolean
}

export interface AppConfig {
  app_name: string
  stages: Record<'requirements' | 'planning' | 'coding' | 'review', { provider: string; model: string }>
  pi: { thinking_level: string; timeout_seconds: number; max_concurrent_runs: number; agent_dir: string }
  repo: { local_path: string; remote: string; target_branch: string; runs_root: string; config_path: string }
  repos: RepoConfig[]
  delivery: {
    pr_creation_enabled: boolean
    bitbucket_workspace: string
    bitbucket_repo_slug: string
    jira_base_url: string
    jira_comments_enabled: boolean
    jira_comment_channel_enabled: boolean
    jira_trigger_label: string
  }
  review: { max_iterations: number; max_diff_chars: number; rules_path: string; rules: string }
  persistence: { checkpointer: string; queue: string }
}

export type Decision =
  | { action: 'approve'; requirements?: Requirements; acknowledge_scope?: boolean }
  | { action: 'revise'; notes: string }
  | { action: 'cancel' }
  | { action: 'approve'; mode: 'all' | 'phased' }
  | { action: 'refine'; notes: string }
  | { action: 'reject' }
  | { action: 'continue' }
  | { action: 'stop' }
  | { action: 'create_pr'; pr_title: string; pr_description?: string }
  | { action: 'finish' }

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  const text = await res.text()
  let body: unknown = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = null
  }
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail
    throw new ApiError(res.status, typeof detail === 'string' ? detail : text || `HTTP ${res.status}`)
  }
  return body as T
}

export const api = {
  config: () => request<AppConfig>('/api/config'),
  listRuns: () => request<RunSummary[]>('/api/runs'),
  startRun: (
    issue_key: string,
    inline?: { summary: string; description: string; project_key?: string },
    repos?: string[],
  ) =>
    request<RunStatus>('/api/runs', {
      method: 'POST',
      body: JSON.stringify({ issue_key, ...(inline ?? {}), ...(repos?.length ? { repos } : {}) }),
    }),
  getRun: (key: string) => request<RunStatus>(`/api/runs/${encodeURIComponent(key)}`),
  decide: (key: string, gateId: string, decision: Decision) =>
    request<RunStatus>(`/api/runs/${encodeURIComponent(key)}/decision`, {
      method: 'POST',
      body: JSON.stringify({ ...decision, gate_id: gateId }),
    }),
  retry: (key: string) => request<RunStatus>(`/api/runs/${encodeURIComponent(key)}/retry`, { method: 'POST' }),
}
