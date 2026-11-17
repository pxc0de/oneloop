import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Row } from '../types'

// A thin client of the `oneloop` CLI: everything works without this mod, and
// nothing here enforces anything. It reads `oneloop status --json` and calls
// the same commands a person would type.

const PANE = 'oneloop'
const POLL_MS = 15_000
const DASHBOARD = 'http://127.0.0.1:4747/'

// Change the shape tag when Row's fields change: it declines what an earlier
// version stored, which outlives a reload.
const rows = atom({ plugin: 'oneloop', key: 'rows' } as const, [], { shape: 'rows-1' })
const error = atom({ plugin: 'oneloop', key: 'error' } as const, '')
const toasted = atom({ plugin: 'oneloop', key: 'toasted' } as const, [])

const KIND: Record<string, string> = {
  spec_questions: 'questions on the issue',
  approval: 'approve to merge',
  couldnt_run: "couldn't run",
  cap_reached: 'round cap reached',
  budget_reached: 'budget reached',
  builder_blocked: 'builder blocked',
  conflict: 'merge conflict',
  changed_under_us: 'changed by someone',
  uncertain_effect: 'unconfirmed GitHub write',
  paused: 'paused',
  error: 'internal error',
  stale: 'process died',
}

const key = (r: Row) => `${r.repo}#${r.issue}`
const needsYou = (r: Row) => r.status === 'needs_human' || r.liveness === 'stale'
const kindOf = (r: Row) => (r.needs_human ? r.needs_human.kind : 'stale')

function ago(ts: number, now: number): string {
  const s = Math.max(0, now / 1000 - ts)
  if (s < 3600) return `${Math.round(s / 60)}m`
  if (s < 86400) return `${(s / 3600).toFixed(1)}h`
  return `${(s / 86400).toFixed(1)}d`
}

async function bin($: EngineInterface): Promise<string> {
  return (await $.env.get('ONELOOP_BIN')) || 'oneloop'
}

async function oneloop($: EngineInterface, args: string[]) {
  return $.process.run([await bin($), ...args], { timeoutMs: 60_000 })
}

async function refresh($: EngineInterface): Promise<void> {
  let list: Row[]
  try {
    const out = await oneloop($, ['status', '--json'])
    if (out.exitCode !== 0) throw new Error(out.stderr.trim().split('\n').pop() || `exit ${out.exitCode}`)
    list = JSON.parse(out.stdout) as Row[]
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    await update($, error, () => `oneloop status failed: ${msg}`)
    $.ui.status('oneloop ▸ unavailable')
    return
  }
  await update($, error, () => '')
  await update($, rows, () => list)

  const waiting = list.filter(needsYou)
  const running = list.filter(r => r.status === 'running' && r.liveness === 'active')
  if (!list.length) {
    $.ui.status(undefined)
  } else {
    const parts = [`${running.length} running`]
    if (waiting.length) parts.push(`⚑ ${waiting.length} need${waiting.length === 1 ? 's' : ''} you`)
    $.ui.status(`oneloop ▸ ${parts.join(' · ')}`)
  }

  // One toast per item, the first time it needs you.
  const before = new Set(await read($, toasted))
  const fresh = waiting.filter(r => !before.has(key(r) + kindOf(r)))
  for (const r of fresh.slice(0, 3)) {
    $.ui.toast(`oneloop ⚑ ${key(r)}: ${KIND[kindOf(r)] ?? kindOf(r)}. /oneloop to act`)
  }
  await update($, toasted, () => waiting.map(r => key(r) + kindOf(r)))
}

async function runAction($: EngineInterface, args: string[]): Promise<string> {
  const out = await oneloop($, args)
  await refresh($)
  const text = (out.stdout + out.stderr).trim()
  return text || (out.exitCode === 0 ? 'done' : `exit ${out.exitCode}`)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'oneloop',
      description: 'oneloop inbox. Also: /oneloop approve|resume|cancel <issue> [owner/repo], /oneloop sendback <issue> <note>',
    })
    void refresh($)
    $.clock.every(POLL_MS, () => void refresh($))
    return next(e)
  })

  on('command.run', { command: 'oneloop' }, async ($, e) => {
    const [verb, issue, ...rest] = e.args.trim().split(/\s+/).filter(Boolean)
    if (!verb) {
      await refresh($)
      const opened = await $.ui.open({ id: PANE, title: 'oneloop' })
      return { text: opened.isPlaced ? 'oneloop inbox opened.' : `oneloop: ${opened.reason ?? 'pane not placed'}` }
    }
    if (!issue || !/^\d+$/.test(issue)) {
      return { text: 'usage: /oneloop [approve|resume|cancel <issue> [owner/repo] | sendback <issue> <note>]' }
    }
    const list = await read($, rows)
    const match = list.filter(r => String(r.issue) === issue)
    const named = verb !== 'sendback' && rest[0]?.includes('/') ? rest[0] : undefined
    const repoArg = named ?? (match.length === 1 ? match[0]?.repo : undefined) ?? ''
    const repo = repoArg ? ['--repo', repoArg] : []
    if (verb === 'sendback') {
      if (!rest.length) return { text: 'usage: /oneloop sendback <issue> <what to change>' }
      return { text: await runAction($, ['sendback', issue, rest.join(' '), ...repo, '--background']) }
    }
    if (verb === 'approve' || verb === 'resume') {
      return { text: await runAction($, [verb, issue, ...repo, '--background']) }
    }
    if (verb === 'cancel') {
      return { text: await runAction($, ['cancel', issue, ...repo]) }
    }
    return { text: `unknown action ${verb}` }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    const list = await read($, rows)
    const problem = await read($, error)
    const now = await $.clock.now()
    const waiting = list.filter(needsYou).sort((a, b) => (a.needs_human?.since ?? a.updated_at) - (b.needs_human?.since ?? b.updated_at))
    const running = list.filter(r => r.status === 'running' && r.liveness === 'active')
    const width = Math.max(40, (e.viewport?.columns ?? 80) - 4)
    const clip = (s: string, n = width) => (s.length > n ? s.slice(0, n - 1) + '…' : s)

    const act = (args: string[]) => () => void runAction($, args).then(text => $.ui.toast(clip(text, 120)))
    const openUrl = (url: string) => () => void $.process.run(['open', url])

    return (
      <Box flexDirection="column">
        {problem && <Text color="red">{clip(problem)}</Text>}
        <Text dimColor>
          {running.length} running · {waiting.length} need{waiting.length === 1 ? 's' : ''} you · refreshes every {POLL_MS / 1000}s
        </Text>
        <Text> </Text>
        <Text bold>⚑ Needs you</Text>
        {waiting.length === 0 && <Text dimColor>  Nothing needs you right now.</Text>}
        {waiting.map((r, i) => {
          const kind = kindOf(r)
          const reason = r.needs_human?.reason ?? 'The process for this run stopped; resume continues it.'
          const repo = ['--repo', r.repo]
          const first = i === 0
          return (
            <Box flexDirection="column" marginBottom={1}>
              <Text>
                <Text bold>{key(r)}</Text> {clip(r.title, Math.max(10, width - key(r).length - 30))}
                <Text dimColor>  {KIND[kind] ?? kind}{r.risk ? ` · ${r.risk} risk` : ''} · waiting {ago(r.needs_human?.since ?? r.updated_at, now)}</Text>
              </Text>
              <Text dimColor>  {clip(reason, width - 2)}</Text>
              <Box>
                {kind === 'approval' ? (
                  <Button key={`approve-${key(r)}`} label="Approve & merge" hotkey={first ? '1' : undefined} variant="primary"
                    onPress={act(['approve', String(r.issue), ...repo, '--background'])} />
                ) : (
                  <Button key={`resume-${key(r)}`} label="Resume" hotkey={first ? '1' : undefined} variant="primary"
                    onPress={act(['resume', String(r.issue), ...repo, '--background'])} />
                )}
                <Text> </Text>
                {r.pr_url ? (
                  <Button key={`pr-${key(r)}`} label="Open PR" hotkey={first ? '2' : undefined} onPress={openUrl(r.pr_url)} />
                ) : null}
                <Text> </Text>
                <Button key={`dash-${key(r)}`} label="Dashboard" hotkey={first ? '3' : undefined}
                  onPress={openUrl(`${DASHBOARD}#/run/${r.repo.replace('/', '__')}/${r.issue}`)} />
              </Box>
              {first && <Text dimColor>  Send back: /oneloop sendback {r.issue} {'<what to change>'}</Text>}
            </Box>
          )
        })}
        <Text bold>● Running</Text>
        {running.length === 0 && <Text dimColor>  Nothing running.</Text>}
        {running.map(r => (
          <Text>
            {'  '}<Text bold>{key(r)}</Text> {r.phase.toUpperCase()}
            <Text dimColor> · ${r.cost_usd.toFixed(2)} · {clip(r.activity || r.status, Math.max(10, width - key(r).length - 30))}</Text>
          </Text>
        ))}
      </Box>
    )
  })
}
