import { expect, test } from 'claude-code/testing'

const PANE = {
  plugin: 'oneloop',
  component: 'Pane',
  requestId: 'oneloop',
  props: {
    title: 'oneloop',
    isFocused: true,
    bodyColumns: 100,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 30 },
    view: {},
  },
} as const

const NOW = 1_800_000_000_000

const ROWS = [
  {
    repo: 'acme/api', issue: 13, title: 'Rotate session signing keys', status: 'needs_human', phase: 'land',
    liveness: 'idle', needs_human: { kind: 'approval', reason: 'high risk: touches REVIEW.md', detail: '', since: NOW / 1000 - 600 },
    risk: 'high', cost_usd: 4.5, pr_url: 'https://github.com/acme/api/pull/7', activity: '', rounds: {},
    created_at: NOW / 1000 - 3600, updated_at: NOW / 1000 - 600,
  },
  {
    repo: 'acme/web', issue: 91, title: 'Debounce search input', status: 'running', phase: 'verify',
    liveness: 'active', needs_human: null, risk: null, cost_usd: 1.2, pr_url: null,
    activity: 'Bash `npm test`', rounds: {}, created_at: NOW / 1000 - 300, updated_at: NOW / 1000 - 5,
  },
]

test('the inbox shows what needs you first, with approve on key 1', async ($, on) => {
  const ran: string[][] = []
  const toasts: string[] = []
  const statuses: (string | undefined)[] = []
  on('command.register', () => ({ value: { command: 'oneloop' } }))
  on('env.get', () => ({ value: undefined }))
  on('clock.now', () => ({ value: NOW }))
  on('clock.every', () => ({ value: undefined }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.status', (_, e) => { statuses.push(e.text); return { value: undefined } })
  on('ui.toast', (_, e) => { toasts.push(e.text); return { value: undefined } })
  on('process.run', (_, e) => {
    ran.push([...e.argv])
    return { value: { exitCode: 0, stdout: JSON.stringify(ROWS), stderr: '' } }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))

  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  // /oneloop refreshes before it opens the pane, so the rows are in by now.
  const opened = await $.command.run({ command: 'oneloop', args: '' })
  expect(opened.text).toBe('oneloop inbox opened.')

  expect(ran[0]).toEqual(['oneloop', 'status', '--json'])
  expect(statuses.at(-1)).toBe('oneloop ▸ 1 running · ⚑ 1 needs you')
  expect(toasts.length).toBe(1)

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const approve = await ui.find({ type: 'Button', key: 'approve-acme/api#13' })
  expect(approve?.props.hotkey).toBe('1')
  expect((await ui.find({ type: 'Text', text: /Debounce search input|acme\/web#91/ })) != null).toBe(true)
  await ui.unmount()
})
