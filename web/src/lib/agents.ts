/* The agent's sub-agents across every conversation: GET /api/sub-agents, again on each
   `agent.state` of the feed. App fetches the whole list once and shares it (AgentsContext).
   Stop is POST /api/sub-agents/{id}/stop. */
import { createContext } from 'react'
import { post, request, type Crumb, type Display, type Home } from './api'
import { useResource } from './useResource'

export type Agent = {
  id: string
  /** the conversation that started it */
  parent: string
  name: string
  host: string
  access: 'read' | 'work'
  folder: string | null
  /** at work (its last turn has not reported) */
  working: boolean
  /** where it stands (server/celine/core/statuses.py AgentState), that state as the page
      shows it, and why it failed, stopped or paused */
  state: AgentState
  display: Display
  reason: string | null
  /** the State option of a list it falls under (GET /api/sub-agents `filters`) */
  filter: string
  /** its model's label, its agent CLI, the device it runs on */
  model: string | null
  cli: string | null
  device: string | null
  /** its last words, before a report */
  last: string | null
  /** the tool its live turn runs, null between steps */
  step: string | null
  /** that step in words, the server's ("Reading the note") */
  step_words: string | null
  /** its latest report */
  report?: { id: number; text: string } | null
  created_at: string
  updated_at: string
  /** where it lives (its root's home) and the path that opens it, the server's */
  home: Home
  link: string
}

/** where a sub-agent stands, the server's value for it */
export type AgentState = 'queued' | 'working' | 'done' | 'stopped' | 'failed' | 'paused'
/** a sub-agents list: its rows and the State options with their labels and counts, the server's */
export type Agents = { agents: Agent[]; filters: { value: string; label: string; count: number }[] }

/** one sub-agent in the tree under a conversation's top (GET /api/conversations/{id}/sub-agents,
    server/celine/sub_agents/store.py tree_of) */
export type TreeAgent = {
  id: string
  parent: string
  /** 1: started by the top conversation */
  depth: number
  name: string
  state: AgentState
  /** that state as the page shows it */
  display: Display
  /** why it failed, stopped or paused, the server's words */
  reason: string | null
  step_words: string | null
  model: string | null
  prompt_tokens: number
  completion_tokens: number
  seconds: number
  link: string
}

/** a mission the top or one of its sub-agents started, in the same tree: its status,
    and its note as the reason while it waits for you or stopped */
export type TreeMission = Omit<TreeAgent, 'state' | 'step_words'> & {
  state: 'active' | 'needs_you' | 'done' | 'stopped'
}

export const agentsApi = {
  /** every one not in the archive, or with `home` those that live there (`missions`) */
  list: (home?: string) => request<Agents>(`/sub-agents${home ? `?home=${encodeURIComponent(home)}` : ''}`),
  /** `origin`: where the top came from (the run that started a mission); `total`: what the
      top and everything under it took */
  tree: (cid: string) => request<{
    top: string; origin: Crumb[]; agents: TreeAgent[]; missions: TreeMission[]
    total: { prompt_tokens: number; completion_tokens: number; seconds: number }
  }>(`/conversations/${encodeURIComponent(cid)}/sub-agents`),
  stop: (id: string) => post<{ stopped: boolean }>(`/sub-agents/${encodeURIComponent(id)}/stop`),
}

const NONE: Agents = { agents: [], filters: [] }

/** a sub-agents list as the server has it now: fetched, and again when a sub-agent's state changes */
export function useAgents(home?: string): Agents {
  return useResource(() => agentsApi.list(home), [home], { events: ev => ev.type === 'agent.state' }).data ?? NONE
}

/** every sub-agent, App's one list: for the tool rows that started them and the pages that show them */
export const AgentsContext = createContext<Agent[]>([])
