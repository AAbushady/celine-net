/* The agent's sub-agents across every conversation: GET /api/sub-agents, again on each
   `agent.state` of the feed. App fetches the whole list once and shares it (AgentsContext).
   Stop is POST /api/sub-agents/{id}/stop. The types are the server's route models
   (api.gen.ts, from server/celine/sub_agents/api.py). */
import { createContext } from 'react'
import { post, request } from './api'
import type { Stopped, SubAgent, SubAgentList, SubAgentTree } from './api.gen'
import { useResource } from './useResource'

export type { TreeAgent, TreeMission } from './api.gen'
/** one sub-agent as the overview shows it */
export type Agent = SubAgent
/** a sub-agents list: its rows and the State options with their labels and counts */
export type Agents = SubAgentList

export const agentsApi = {
  /** every one not in the archive, or with `home` those that live there (`missions`) */
  list: (home?: string) => request<SubAgentList>(`/sub-agents${home ? `?home=${encodeURIComponent(home)}` : ''}`),
  /** everything under the top of `cid`'s chain; `origin`: where the top came from (the run
      that started a mission); `total`: what the top and everything under it took */
  tree: (cid: string) => request<SubAgentTree>(`/conversations/${encodeURIComponent(cid)}/sub-agents`),
  stop: (id: string) => post<Stopped>(`/sub-agents/${encodeURIComponent(id)}/stop`),
}

const NONE: Agents = { agents: [], filters: [] }

/** a sub-agents list as the server has it now: fetched, and again when a sub-agent's state changes */
export function useAgents(home?: string): Agents {
  return useResource(() => agentsApi.list(home), [home], { events: ev => ev.type === 'agent.state' }).data ?? NONE
}

/** every sub-agent, App's one list: for the tool rows that started them and the pages that show them */
export const AgentsContext = createContext<Agent[]>([])
