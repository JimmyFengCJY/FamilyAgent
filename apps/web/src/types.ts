export type Mode = "meeting" | "planning" | "support" | "finance" | "assistant";
export type View =
  | "home"
  | "chat"
  | "meetings"
  | "planning"
  | "finance"
  | "tasks"
  | "roles"
  | "memory"
  | "runs"
  | "settings";
export interface User {
  id: string;
  name: string;
  email: string;
  household: string;
}
export interface Character {
  id: string;
  name: string;
  capability: "host" | "planner" | "support" | "finance" | "assistant";
  tone: string;
  description: string;
  color: string;
  initial: string;
  version: number;
  builtin: boolean;
}
export interface Result {
  summary: string;
  proposals: string[];
  assumptions: string[];
  missing_information: string[];
  risks: string[];
  suggested_actions: string[];
  consensus?: string[];
  disagreements?: { topic: string; positions: { source_id: string; quote: string }[]; question: string }[];
  expert_sources?: { source_id: string; actor_id: string; actor_name: string }[];
}
export interface Conversation {
  id: string;
  title: string;
  mode: Mode;
  scope: string;
  participants: string[];
  character_ids: string[];
  character_snapshots: Character[];
  use_memory: boolean;
  orchestration?: "single" | "multi" | "independent";
  constraints?: string[];
  status: string;
  updated_at: string;
  owner_id: string;
}
export interface Message {
  memory_sources?: { id: string; score: number; status: "available" | "changed" | "unavailable"; content?: string; source?: string; created_at?: string }[];
  id: string;
  actor_type: string;
  actor_name: string;
  actor_id: string;
  content: string;
  created_at: string;
  color?: string;
  provider?: string;
  stage?: string;
  result?: Result;
}
export interface Run {
  pending_tool?: { id: string; name: string; state: string };
  available_tools?: string[];
  id: string;
  owner_id: string;
  conversation_id: string;
  mode: Mode;
  status: string;
  cursor: number;
  calls: number;
  max_calls: number;
  created_at: string;
  provider: string;
  version: number;
  error?: string;
  steps: { character: Character; stage: string }[];
  usage: { input_tokens: number; output_tokens: number };
  participants: string[];
  approvals: Record<string, boolean>;
  intent?: {
    name: string;
    label: string;
    source: string;
    reason: string;
    allow_actions: boolean;
  };
  decision?: { summary: string; confirmed_at: string };
  created_task_ids?: string[];
  proposal?: {
    kind?: "decision" | "tasks";
    summary: string;
    actions: string[];
    hash: string;
    required_approvers?: string[];
  };
  finance_result?: BudgetResult;
  results: Result[];
}
export interface Task {
  id: string;
  title: string;
  status: string;
  due_at?: string;
  source_run?: string;
  created_at: string;
}
export interface Memory {
  id: string;
  content: string;
  created_at: string;
}
export interface Plan {
  id: string;
  title: string;
  source_run: string;
  conversation_id: string;
  milestones: string[];
  version: number;
  created_at: string;
}
export interface Notification {
  id: string;
  title: string;
  content: string;
  read: boolean;
  created_at: string;
}
export type RunSummary = Pick<Run, "id" | "owner_id" | "conversation_id" | "mode" | "status" | "cursor" | "calls" | "max_calls" | "created_at" | "provider" | "version" | "usage" | "participants" | "approvals">;
export interface MessagePage {
  messages: Message[];
  page: { has_more: boolean; direction: "before" | "after"; oldest: string | null; newest: string | null };
}
export interface State {
  tool_catalog?: { name: string; description: string; risk: string; requires_approval: boolean }[];
  user: User;
  members: User[];
  characters: Character[];
  conversations: Conversation[];
  runs: RunSummary[];
  tasks: Task[];
  plans: Plan[];
  memories: Memory[];
  notifications: Notification[];
  provider: string;
  demo_login: boolean;
}
export interface Detail {
  page?: MessagePage["page"];
  conversation: Conversation;
  messages: Message[];
  runs: Run[];
}
export interface RunEvent {
  id: number;
  type: string;
  data: Record<string, unknown>;
  created_at: string;
}
export interface Budget {
  income: string;
  currency: string;
  items: { name: string; amount: string }[];
}
export interface BudgetResult extends Budget {
  expenses: string;
  remaining: string;
  savings_rate: string | null;
  source: string;
}
