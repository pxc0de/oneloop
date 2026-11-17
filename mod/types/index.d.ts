export type NeedsHuman = { kind: string; reason: string; detail: string; since: number }

/** One run as `oneloop status --json` reports it. */
export type Row = {
  repo: string
  issue: number
  title: string
  status: string
  phase: string
  liveness: string
  needs_human: NeedsHuman | null
  risk: string | null
  cost_usd: number
  pr_url: string | null
  activity: string
  rounds: Record<string, number>
  created_at: number
  updated_at: number
}

declare module 'claude-code' {
  interface PluginState {
    oneloop: {
      rows: Shaped<Row[]>
      error: string
      toasted: string[]
    }
  }
}
