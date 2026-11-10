/* Written from the server's route models (server/celine/page_types.py). Do not edit:
   change the model and write it again with
   `uv run python -m celine.page_types > web/src/lib/api.gen.ts`. */

export type AI = {
  state: "pending" | "done" | "failed" | "skipped"
  model: string | null
  generated_at: number | null
  error: string | null
  stale: boolean
}

export type AgentIn = {
  brief: string
  name: string
  access?: string | null
  folder?: string | null
  model?: string | null
  cli?: string | null
  device?: string | null
}

/** Where a sub-agent stands (sub_agents/store.py `look` works it out; nothing stores it). */
export type AgentState = "queued" | "working" | "done" | "stopped" | "failed" | "paused"

export type AskBody = {
  of: string
  prompt: string
  text?: string
}

export type Backlink = {
  id: number
  path: string
  title: string
}

export type Body_upload_api_documents_post = {
  files: string[]
  paths?: string[]
  modes?: string[]
  folder?: string
  title?: string
  description?: string
}

export type Body_upload_attachment_api_attachments_post = {
  file: string
}

export type BranchBody = {
  message_id: string
}

export type BulkIn = {
  what: "conversations" | "documents" | "folders" | "notes" | "chats" | "terminals"
  action: "archive" | "unarchive" | "trash" | "restore" | "purge"
  ids?: string[]
  older_than?: number | null
  kind?: ("chat" | "mission") | null
  dry_run?: boolean
}

export type Call = {
  tool: string
  args?: Record<string, unknown>
}

/** Only the fields sent are changed; `name: null` clears your name. */
export type ChatPatch = {
  name?: string | null
  trashed?: boolean | null
}

export type ContinueBody = {
  wait?: boolean
}

/** One conversation of a path from the top down (homes.path). */
export type ConversationCrumb = {
  id: string
  kind: "chat" | "mission" | "run" | "background" | "agent"
  name: string
  link: string | null
}

export type ConversationPatch = {
  title?: string | null
  pinned?: boolean | null
  notes_off?: string[] | null
}

export type Crumb = {
  name: string
  path: string
}

export type DefaultModes = {
  modes: ("chunked" | "full_file")[]
}

export type DefaultModesBody = {
  files: PickedFile[]
}

export type Deleted = {
  deleted: number
  id: number
}

/** A status as the page shows it (core/statuses.py). */
export type Display = {
  words: string
  tone: Tone
}

export type EditBody = {
  text: string
}

export type Existing = {
  name: string
  item: Item
  asked: ("chunked" | "full_file") | null
}

/** One State option of a list: the value a row's `filter` holds, the label the page shows and how many of the rows sent fall under it. */
export type FilterOption = {
  value: string
  label: string
  count: number
}

export type Folder = {
  path: string
  name: string
  items: number
  size: number
  updated_at: number
  added_at: number
  trashed_at: number | null
}

export type FolderPage = {
  folder: Folder
  breadcrumbs: Crumb[]
  folders: Folder[]
  items: Item[]
  next: string | null
}

export type FolderPatch = {
  name?: string | null
  folder?: string | null
  trashed?: boolean | null
}

export type GroupOut = {
  name: string
  addresses: string[]
  threads: number
  waiting: number
  items: ThreadOut[]
  more: number
}

export type ImageItemIn = {
  prompt: string
  source?: string | null
  preset?: string | null
  negative?: string | null
  width?: number | null
  height?: number | null
  seed?: number | null
  model?: string | null
  loras?: string[] | null
  mode?: string | null
}

export type ImagesIn = {
  kind: "images"
  items: ImageItemIn[]
  count?: number
}

export type ImpersonateBody = {
  start?: string
}

export type IncludeBody = {
  key: string
}

export type Item = {
  id: number
  path: string
  folder: string
  name: string
  title: string
  file_title: string
  user_title: string | null
  user_description: string | null
  ai_description: string | null
  ai: AI
  kind: string
  mode: "chunked" | "full_file"
  mime: string | null
  size: number
  pages: number | null
  n_segments: number
  n_chars: number
  n_words: number
  state: "pending" | "ready" | "failed"
  error: string | null
  sha256: string
  added_at: number
  updated_at: number
  author: "user" | "celine"
  trashed: boolean
}

export type ItemDetail = {
  id: number
  path: string
  folder: string
  name: string
  title: string
  file_title: string
  user_title: string | null
  user_description: string | null
  ai_description: string | null
  ai: AI
  kind: string
  mode: "chunked" | "full_file"
  mime: string | null
  size: number
  pages: number | null
  n_segments: number
  n_chars: number
  n_words: number
  state: "pending" | "ready" | "failed"
  error: string | null
  sha256: string
  added_at: number
  updated_at: number
  author: "user" | "celine"
  trashed: boolean
  backlinks: Backlink[]
}

export type ItemPatch = {
  title?: string | null
  description?: string | null
  mode?: ("chunked" | "full_file") | null
  name?: string | null
  folder?: string | null
  trashed?: boolean | null
}

export type ItemsPage = {
  items: Item[]
  next: string | null
}

export type Keys = {
  p256dh: string
  auth: string
}

export type LinkBody = {
  refs: string[]
  name: string
}

export type MayType = {
  may_type: boolean
}

export type MessageIn = {
  to: string
  text?: string
  subject?: string | null
  reply_to?: number | null
  in?: string | null
  at?: string | null
  until?: string | null
}

/** Each given only to change it. `steps` (its plan's, a step's model changed) go to its plan (plans/store.py), not to it as a message. */
export type MissionEdit = {
  name?: string | null
  brief?: string | null
  report_to?: string | null
  time_limit?: string | null
  min_time?: string | null
  check_in?: string | null
  steps?: StepIn[] | null
}

/** The brief; with no name it has one from the brief; `model` its chat model (a ref), else the default. */
export type MissionIn = {
  name?: string | null
  model?: string | null
  brief?: string
  report_to?: string | null
  time_limit?: string | null
  min_time?: string | null
  check_in?: string | null
}

/** Where a mission stands (missions.status). */
export type MissionStatus = "active" | "needs_you" | "done" | "stopped"

export type MusicIn = {
  kind: "music"
  items: MusicItemIn[]
  count?: number
}

export type MusicItemIn = {
  style: string
  lyrics?: string | null
  title?: string | null
  instrumental?: boolean
  duration?: number | null
  seed?: number | null
}

export type NewFolder = {
  path: string
}

export type NewMessage = {
  text?: string
  attachments?: string[]
}

export type NotebookChange = {
  path?: string | null
  protected?: boolean | null
}

export type NotebookPath = {
  path: string
}

export type NotebookWrite = {
  content: string
}

export type On = {
  on: boolean
}

export type Override = {
  enabled: boolean
  client_seq?: number
}

export type PersonBody = {
  name?: string | null
  about?: string | null
  aliases?: string[] | null
}

export type PersonPatch = {
  name?: string | null
}

export type PickedFile = {
  name: string
  size: number
}

export type ReadIn = {
  ids: number[]
}

export type ReindexBody = {
  describe?: boolean
}

export type Reindexed = {
  ok: boolean
  item: Item
}

export type Rejected = {
  name: string
  reason: string
}

/** A sub-agent's report up: the intercom message's id and its text. */
export type Report = {
  id: number
  text: string
}

export type RunBody = {
  text?: string
}

export type Segment = {
  id: number
  ref: string
  seq: number
  kind: string
  page: number | null
  heading: string | null
  t_ms: number | null
  speaker: string | null
  text: string
}

export type Segments = {
  segments: Segment[]
  has_before: boolean
  has_after: boolean
}

export type Started = {
  conversation_id: string
  resumed: boolean
}

/** A step of its plan as the page sends it back (plans/store.py). */
export type StepIn = {
  title: string
  state?: string
  checkpoint?: string
  model?: string | null
}

export type Stopped = {
  stopped: boolean
}

/** One sub-agent as the overview shows it (store.agents_of). */
export type SubAgent = {
  id: string
  /** the conversation that started it */
  parent: string
  name: string
  access: ("read" | "work") | null
  folder: string | null
  /** its model's label */
  model: string | null
  /** its agent CLI in the box */
  cli: string | null
  /** the device it runs on */
  device: string | null
  /** at work: queued, or something will still come of it */
  working: boolean
  state: AgentState
  display: Display
  /** why it failed, stopped or paused */
  reason: string | null
  /** the State option of the list it falls under */
  filter: AgentState
  /** the tool its live turn runs */
  step: string | null
  /** that step in words */
  step_words: string | null
  /** its latest report */
  report: Report | null
  /** its last words, before a report */
  last: string | null
  created_at: string
  updated_at: string
  /** where it lives, its root's home */
  home: "agent" | "missions" | "routines"
  /** the app path that opens it */
  link: string
}

/** A sub-agents list with the State options of its rows. */
export type SubAgentList = {
  agents: SubAgent[]
  filters: FilterOption[]
}

/** Everything under the top of a conversation's chain. */
export type SubAgentTree = {
  top: string
  /** where the top came from, a mission's run */
  origin: ConversationCrumb[]
  agents: TreeAgent[]
  missions: TreeMission[]
  /** what the top and everything under it took */
  total: Work
}

/** `enabled: false` pauses a running subscription; starting is POST .../start. */
export type SubPut = {
  enabled?: boolean | null
  settings?: Record<string, unknown> | null
  guild_name?: string | null
  icon?: string | null
}

/** A browser's PushSubscription as toJSON() gives it, and the notice kinds it takes. */
export type SubscriptionBody = {
  endpoint: string
  keys: Keys
  kinds: string[]
}

export type Target = {
  message_id: string
}

export type TargetSet = {
  set: Record<string, boolean>
}

export type TerminalMessage = {
  text?: string
  conversation_id?: string | null
  command_id?: string | null
}

export type TextBody = {
  text: string
}

export type ThreadOut = {
  thread: number
  name: string
  subject: string | null
  source: Record<string, unknown> | null
  count: number
  unread: number
  last: Record<string, unknown>
}

export type ThreadsIn = {
  threads: number[]
}

export type ThreadsPage = {
  groups: GroupOut[]
  next: string | null
}

export type TokenBody = {
  token: string
}

export type TokenIn = {
  name: string
}

/** The tone a status is shown in (the page's StateWord tones). */
export type Tone = "running" | "warn" | "error" | "success" | "muted"

export type Trash = {
  folders: Folder[]
  items: Item[]
}

/** One sub-agent in the tree under a top conversation (store.tree_of). */
export type TreeAgent = {
  id: string
  kind: "agent"
  parent: string
  /** 1: started by the top conversation */
  depth: number
  name: string
  state: AgentState
  display: Display
  /** why it failed, stopped or paused */
  reason: string | null
  step_words: string | null
  /** the newest model that answered, as its label */
  model: string | null
  prompt_tokens: number
  completion_tokens: number
  seconds: number
  link: string
}

/** A mission the top or one of its sub-agents started, in the same tree. */
export type TreeMission = {
  id: string
  kind: "mission"
  parent: string
  depth: number
  name: string
  state: MissionStatus
  display: Display
  /** its note while it waits for you or stopped */
  reason: string | null
  step_words: string | null
  model: string | null
  prompt_tokens: number
  completion_tokens: number
  seconds: number
  link: string
}

export type UploadIn = {
  kind: "images" | "music"
  label: string
  workflow: string
}

export type UploadResult = {
  items: Item[]
  existing: Existing[]
  rejected: Rejected[]
  task: string | null
}

export type ValuesIn = {
  values: Record<string, unknown>
}

/** What turns took: tokens in and out as the servers counted them, and run time. */
export type Work = {
  prompt_tokens: number
  completion_tokens: number
  seconds: number
}

export type WorkflowIn = {
  workflow: string
}

export type Written = {
  path: string
  text: string
  item: Item | null
}

export type celine__browser__api__Control = {
  action: string
  tab?: string | null
}

export type celine__terminals__api__Control = {
  action: string
}
