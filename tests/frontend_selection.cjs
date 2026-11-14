const assert = require('node:assert/strict')
const { test } = require('node:test')
const { readFileSync } = require('node:fs')
const vm = require('node:vm')
const path = require('node:path')

const atom = initial => ({ value: initial, get() { return this.value }, set(value) { this.value = value } })
const jsx = (type, props) => ({ type, props })
const source = readFileSync(path.join(__dirname, '../desktop/plugin.js'), 'utf8')
  .replace(/import\s+[\s\S]*?from\s+['"][^'"]+['"]\s*/g, '')
  .replace('export default', 'const plugin =')

// A fake Hermes Desktop: app-level storage, a switchable profile/route/epoch and recorded notifications.
function makeEnv({ profile = 'A', routes = [], epoch = 1, model = 'claude' } = {}) {
  const store = new Map()
  const queries = new Map()
  const env = {
    epoch, routes, store, queries, notifications: [], osNotifications: [], registered: [],
    profile: atom(profile), gateway: atom('open'), restCalls: [],
    restImpl: async () => { throw new Error('offline') }
  }
  const clone = value => JSON.parse(JSON.stringify(value))
  const ctx = {
    storage: {
      get: (key, fallback) => (store.has(key) ? clone(store.get(key)) : fallback),
      set: (key, value) => store.set(key, clone(value)),
      remove: key => store.delete(key)
    },
    rest: (route, options) => { env.restCalls.push(route); return env.restImpl(route, options) },
    os: { notify: notification => env.osNotifications.push(notification) },
    register: item => env.registered.push(item)
  }
  const context = vm.createContext({
    atom, jsx, jsxs: jsx, Button: 'button', cn: (...values) => values.filter(Boolean).join(' '),
    useState: value => [value, () => {}], useValue: value => value.get(),
    useQuery: options => ({ data: queries.get(JSON.stringify(options.queryKey)), options }),
    queryClient: {
      getQueryData: key => queries.get(JSON.stringify(key)),
      setQueryData: (key, value) => queries.set(JSON.stringify(key), value)
    },
    STATUSBAR_AREAS: { right: 'right' },
    sdk: { gatewayActivationEpoch: () => env.epoch },
    ctx,
    host: {
      state: {
        focusedSessionId: atom('s'), activeSessionId: atom('s'), model: atom(model),
        profile: env.profile, gateway: env.gateway
      },
      onEvent() {},
      notify: notification => env.notifications.push(notification),
      profileRoutes: () => env.routes
    }
  })
  vm.runInContext(source, context)
  env.run = expression => vm.runInContext(expression, context)
  // Plain host-realm copy of a result (deepStrictEqual compares prototypes across vm realms).
  env.json = expression => JSON.parse(vm.runInContext('JSON.stringify(' + expression + ')', context))
  env.set = (name, value) => { context[name] = value }
  return env
}

function loadPlugin(data, saved = '__auto_session__', provider = 'anthropic') {
  const env = makeEnv()
  env.queries.set(JSON.stringify(env.run('readUsageScope().queryKey')), data)
  env.set('input', { data, saved, provider })
  env.run('$statusSelection.set(input.saved); $runtimeInfoBySession.set({s: {provider: input.provider}})')
  return env.run
}
const window = (label, remaining_percent, extra = {}) => ({ label, remaining_percent, ...extra })
const provider = (id, windows) => ({ provider: id, label: id, available: true, windows })
function nodes(tree) {
  if (!tree || typeof tree !== 'object') return []
  return [tree, ...[tree.props?.children].flat().flatMap(nodes)]
}
const select = (data, saved = '__auto_session__', active = 'anthropic') =>
  loadPlugin(data, saved, active)('selectedStatus(input.data, input.saved, {usageProvider: usageProviderId(input.provider, "claude")})')

test('missing session percentage stays selected and renders no data, never zero or weekly', () => {
  for (const missing of [null, undefined]) {
    const data = { providers: [provider('anthropic', [window('Weekly', 92), window('5-hour window', missing)])] }
    for (const saved of ['__auto_session__', 'anthropic::Weekly']) {
      const selected = select(data, saved).selected
      assert.equal(selected.window, '5-hour window')
      assert.equal(selected.remaining, null)
      const label = loadPlugin(data, saved)('UsageStatusLabel()')
      assert.equal(label.props.children, 'AI —')
      assert.match(label.props.title, /5-hour window/)
    }
  }
  const data = { providers: [provider('anthropic', [window('Session', 0), window('Weekly', 92)])] }
  assert.equal(loadPlugin(data)('UsageStatusLabel()').props.children, 'AI 0.0% · exhausted')
  // An exhausted window still wins over a session without a percentage.
  const blocked = { providers: [provider('anthropic', [window('Weekly', 0), window('5-hour window', null)])] }
  assert.equal(select(blocked).selected.window, 'Weekly')
})

test('weekly then monthly fallback recognizes EN and ES labels', () => {
  for (const weekly of ['Weekly', 'week', 'semana']) {
    for (const monthly of ['Monthly', 'month', 'mensual', 'Subscription period']) {
      // A recognized weekly window is a primary window, so an exhausted month does not decide the chip.
      const data = { providers: [provider('anthropic', [window(monthly, 1), window(weekly, 95)])] }
      assert.equal(select(data).selected.window, weekly)
      // Without session or week, a recognized month wins ties over an unrecognized quota.
      data.providers[0].windows = [window('Other quota', 50), window(monthly, 50)]
      assert.equal(select(data).selected.window, monthly)
      data.providers[0].windows = [window('Other quota', 0), window(monthly, 0)]
      assert.equal(select(data).selected.window, monthly)
    }
  }
})

test('active provider isolation, unsupported provider, empty data and stable ties', () => {
  const data = { providers: [
    provider('openai-codex', [window('Session', 1)]),
    provider('anthropic', [window('Session', 94), window('Current session', 2), window('Weekly', 0)])
  ] }
  assert.equal(select(data).selected.value, 'anthropic::Weekly') // the exhausted week, not a healthy session
  assert.equal(select(data, '__auto_session__', 'openai-codex').selected.remaining, 1)
  assert.equal(select(data, '__auto_session__', 'unsupported').selected, null)
  assert.equal(select(data, 'missing::Weekly', 'unsupported').selected, null)
  assert.equal(select(data, 'missing::Weekly').selected.value, 'anthropic::Weekly')
  assert.equal(select(data, 'missing::Weekly').fellBack, true)
  assert.equal(select(undefined).selected, null)
  assert.equal(select({ providers: [] }).selected, null)
  assert.equal(select(data, 'anthropic::Weekly', 'unsupported').selected.value, 'anthropic::Weekly')
  data.providers[1].windows = [window('Session', 94), window('Current session', 94), window('Weekly', 97)]
  assert.equal(select(data).selected.value, 'anthropic::Session') // equal remaining: the first one is kept
})

test('saved and clicked weekly pin the provider, not its period', () => {
  const data = { providers: [
    provider('anthropic', [window('Weekly', 95), window('Session', 94)]),
    provider('openai-codex', [window('Session', 12)])
  ] }
  for (const saved of ['anthropic::Weekly', 'anthropic::Old label']) {
    assert.equal(select(data, saved, 'openai-codex').selected.value, 'anthropic::Session')
  }
  const run = loadPlugin(data, '__auto_session__', 'openai-codex')
  const tree = run('CompactWindow({provider: input.data.providers[0], window: input.data.providers[0].windows[0], selectedValue: null})')
  const button = nodes(tree).find(node => node.type === 'button')
  assert.match(button.props.title, /provider/i)
  button.props.onClick({preventDefault() {}, stopPropagation() {}})
  assert.equal(run('UsageStatusLabel()').props.children, 'AI 94%')
  const panel = nodes(run('UsageCompactPanel()'))
  assert.ok(panel.some(node => /provider.*exhausted.*session.*week/i.test(node.props?.children)))
})

// U2: the chip follows the backend's availability policy. An exhausted window first (the one that blocks
// longest); otherwise the lowest remaining of session and week, regardless of input order.
test('automatic shows the lowest of session and week, and an exhausted week over a healthy session', () => {
  for (const label of ['Session', 'Current session', '5-hour window', '5 horas', 'sesión']) {
    for (const [session, weekly, expected] of [[94, 92, 'Weekly'], [92, 94, label], [94, 0, 'Weekly'], [0, 94, label]]) {
      for (const windows of [[window('Weekly', weekly), window(label, session)], [window(label, session), window('Weekly', weekly)]]) {
        const data = { providers: [provider('anthropic', windows)] }
        const before = JSON.stringify(data)
        const selected = select(data).selected
        assert.equal(selected.window, expected)
        assert.equal(selected.remaining, Math.min(session, weekly))
        assert.equal(selected.exhausted, Math.min(session, weekly) <= 1)
        assert.equal(JSON.stringify(data), before)
      }
    }
  }
})

test('a healthy session never hides an exhausted week in the chip', () => {
  const data = { providers: [provider('anthropic', [window('Current session', 94), window('Current week', 0)])] }
  for (const saved of ['__auto_session__', 'anthropic::Current session']) {
    const label = loadPlugin(data, saved)('UsageStatusLabel()')
    assert.equal(label.props.children, 'AI 0.0% · exhausted')
    assert.match(label.props.title, /Current week/)
    assert.match(label.props.title, /exhausted/)
  }
  // 1 % or less counts as exhausted, exactly like availability.json.
  data.providers[0].windows[1].remaining_percent = 1
  assert.equal(select(data).selected.exhausted, true)
  data.providers[0].windows[1].remaining_percent = 1.5
  assert.equal(select(data).selected.exhausted, false)
  assert.equal(loadPlugin(data)('UsageStatusLabel()').props.children, 'AI 1.5%')
})

test('with several exhausted windows the chip shows the one that blocks longest', () => {
  const soon = '2026-11-06T15:00:00Z'
  const later = '2026-11-10T00:00:00Z'
  const pick = windows => select({ providers: [provider('anthropic', windows)] }).selected.window
  assert.equal(pick([window('Session', 0, { reset_at: soon }), window('Weekly', 0, { reset_at: later })]), 'Weekly')
  assert.equal(pick([window('Session', 0, { reset_at: later }), window('Weekly', 1, { reset_at: soon })]), 'Session')
  // An unknown reset blocks for an unknown time, so it outranks a known one.
  assert.equal(pick([window('Session', 0, { reset_at: soon }), window('Weekly', 0)]), 'Weekly')
  assert.equal(pick([window('Session', 0), window('Weekly', 0, { reset_at: later })]), 'Session')
  // Same reset: the session (the backend's tie-break).
  assert.equal(pick([window('Weekly', 0, { reset_at: soon }), window('Session', 0, { reset_at: soon })]), 'Session')
  // A monthly bucket does not decide while session or week exist (same as availability.json).
  assert.equal(pick([window('Session', 50), window('Monthly MCP quota', 0)]), 'Session')
})

// N1: cross test with the backend. tests/test_availability.py runs the same fixture through
// _availability_entry; whenever availability.json says exhausted, the chip shows that window exhausted.
test('the chip drops windows without a percentage before choosing, like availability.json', () => {
  const parity = JSON.parse(readFileSync(path.join(__dirname, 'fixtures', 'availability_parity.json'), 'utf8'))
  assert.ok(parity.cases.length >= 7)
  for (const item of parity.cases) {
    for (const windows of [item.windows, [...item.windows].reverse()]) {
      const data = { providers: [provider('zai', windows.map(([label, remaining]) => window(label, remaining)))] }
      const selected = loadPlugin(data, '__auto_session__', 'zai')(
        'selectedStatus(input.data, input.saved, {usageProvider: \'zai\'})').selected
      assert.equal(selected.window, item.chip.window, item.name)
      assert.equal(selected.exhausted, item.chip.exhausted, item.name)
      if (item.backend.state === 'exhausted') assert.equal(selected.exhausted, true, item.name)
      const label = loadPlugin(data, '__auto_session__', 'zai')('UsageStatusLabel()').props.children
      assert.equal(label, item.chip.label ?? (item.chip.exhausted ? 'AI 0.0% · exhausted' : 'AI —'), item.name)
    }
  }
})

test('the cap of the model in use still wins when it is tighter than the session', () => {
  const data = { providers: [provider('anthropic', [
    window('Current session', 50, { scope: 'account' }),
    window('Current week', 60, { scope: 'account' }),
    window('Opus week', 0, { scope: 'model' })
  ])] }
  const run = loadPlugin(data)
  const chosen = model => run(`selectedStatus(input.data, '__auto_session__', {usageProvider: 'anthropic', model: '${model}'}).selected`)
  assert.equal(chosen('claude-opus-5-5').window, 'Opus week')
  assert.equal(chosen('claude-opus-5-5').exhausted, true)
  assert.equal(chosen('claude-sonnet-5-5').window, 'Current session')
  data.providers[0].windows[1].remaining_percent = 0 // account week exhausted too: the cap is not stricter
  assert.equal(chosen('claude-opus-5-5').window, 'Current week')
})

// U8: the behaviour the disclosure texts describe. A pin is a provider (never a window) and it is one
// app-wide preference, not a per-profile one.
test('the pin is a provider and a single app-wide preference', async () => {
  const env = makeEnv({ profile: 'A', routes: [{ profile: 'A', connectionId: 'local-1' }] })
  env.run('plugin.register(ctx)')
  env.run("persistSelection('anthropic::Current week')")
  assert.equal(env.store.get('statusbar-selection-v2'), 'anthropic::Current week')
  assert.deepEqual([...env.store.keys()].filter(key => key.startsWith('statusbar-selection')), ['statusbar-selection-v2'])
  const other = makeEnv({ profile: 'B' }) // another profile reads the same stored pin
  other.store.set('statusbar-selection-v2', env.store.get('statusbar-selection-v2'))
  other.run('plugin.register(ctx)')
  assert.equal(other.run('$statusSelection.get()'), 'anthropic::Current week')
  // The pinned window does not stay: when the data changes, the provider's deciding window is shown.
  const data = { providers: [provider('anthropic', [window('Current session', 10), window('Current week', 60)])] }
  assert.equal(select(data, 'anthropic::Current week', 'openai-codex').selected.window, 'Current session')
})

// --- A7: per-model windows -------------------------------------------------------------------

const modelData = () => ({ providers: [provider('anthropic', [
  window('Current week', 60, { scope: 'account' }),
  window('Opus week', 5, { scope: 'model' }),
  window('Sonnet week', 90, { scope: 'model' })
])] })
const selectFor = (model, saved = '__auto_session__') =>
  loadPlugin(modelData(), saved)(`selectedStatus(input.data, input.saved, {usageProvider: 'anthropic', model: ${JSON.stringify(model)}})`)

test('a model-capped window never speaks for the account in the status bar', () => {
  for (const model of ['claude-haiku-4', '', undefined]) {
    assert.equal(selectFor(model).selected.window, 'Current week')
    assert.equal(selectFor(model).selected.remaining, 60)
  }
  assert.equal(selectFor('claude-sonnet-5-5').selected.remaining, 60) // Sonnet cap (90) is looser than the account
})

test('the cap of the model in use wins only when it is tighter', () => {
  assert.equal(selectFor('claude-opus-5-5').selected.window, 'Opus week')
  assert.equal(selectFor('claude-opus-5-5').selected.remaining, 5)
  assert.equal(selectFor('claude-opus-5-5', 'anthropic::Current week').selected.window, 'Opus week')
  const looser = { providers: [provider('anthropic', [
    window('Current week', 20, { scope: 'account' }), window('Opus week', 80, { scope: 'model' })])] }
  const run = loadPlugin(looser)
  assert.equal(run("selectedStatus(input.data, '__auto_session__', {usageProvider: 'anthropic', model: 'claude-opus-5'}).selected.window"), 'Current week')
})

test('model caps are labelled in the panel and kept out of the fallback summary', () => {
  const run = loadPlugin(modelData())
  const tree = run('CompactWindow({provider: input.data.providers[0], window: input.data.providers[0].windows[1], selectedValue: null})')
  assert.ok(nodes(tree).some(node => node.props?.children === 'Opus week (model cap)'))
  const merged = run(`mergeProviderFallback(
    {providers: [{provider: 'anthropic', available: false, label: 'Claude', windows: []}], summary: {}},
    {providers: [{provider: 'anthropic', available: true, label: 'Claude', details: [], windows: input.data.providers[0].windows}]})`)
  assert.equal(merged.summary.lowest_remaining_percent, 60)
})

// --- A5: cache and responses are scoped to profile + connection and to the route epoch ------------

const payloadFor = (windows, extra = {}) => ({
  fetched_at: '2026-11-06T12:00:00Z',
  summary: { available: 1, total: 1, lowest_remaining_percent: 29 },
  settings: { alerts_enabled: true, alert_warn_percent: 80, alert_critical_percent: 95 },
  providers: [{ provider: 'openai-codex', label: 'OpenAI Codex', available: true, plan: 'Plus', source: 'x',
    fetched_at: '2026-11-06T12:00:00Z', details: [], windows, ...extra }],
  privacy: 'local'
})
const used = (label, usedPercent, extra = {}) => ({
  label, used_percent: usedPercent, remaining_percent: 100 - usedPercent, reset_at: '2026-11-06T15:00:00Z', ...extra
})

test('one profile never sees another profile\'s cached quota', async () => {
  const env = makeEnv({ profile: 'A' })
  env.run('plugin.register(ctx)')
  env.restImpl = async () => payloadFor([used('Session', 29)])
  const a = await env.run('fetchUsage()')
  assert.equal(a.providers[0].windows[0].remaining_percent, 71)
  assert.equal(a.transport_stale, false)

  env.profile.set('B')
  env.restImpl = async () => { throw new Error('backend not loaded in this profile') }
  await assert.rejects(env.run('fetchUsage()'), /backend not loaded/) // not A's 71 % shown under B

  env.profile.set('A')
  const back = await env.run('fetchUsage()')
  assert.equal(back.transport_stale, true) // A's own last-good data is still available to A
  assert.equal(back.providers[0].windows[0].remaining_percent, 71)
})

test('persisted entries carry the profile and connection; the unscoped legacy entry is dropped', async () => {
  const env = makeEnv({ profile: 'work', routes: [{ profile: 'work', connectionId: 'local-1' }, { profile: 'other', connectionId: 'ssh-2' }] })
  env.store.set('last-good-usage', { saved_at: Date.now(), payload: payloadFor([used('Session', 90)]) })
  env.run('plugin.register(ctx)')
  assert.equal(env.store.has('last-good-usage'), false)
  env.restImpl = async () => payloadFor([used('Session', 29)])
  await env.run('fetchUsage()')
  assert.ok(env.store.has('last-good-usage-v2:work|local-1'))
  assert.deepEqual([...env.store.keys()].filter(key => key.startsWith('last-good-usage')), ['last-good-usage-v2:work|local-1'])
})

test('two sources with the same profile name are never persisted', async () => {
  const routes = [{ profile: 'main', connectionId: 'local-1' }, { profile: 'main', connectionId: 'ssh-9' }]
  const env = makeEnv({ profile: 'main', routes })
  env.run('plugin.register(ctx)')
  env.restImpl = async () => payloadFor([used('Session', 29)])
  await env.run('fetchUsage()')
  assert.deepEqual([...env.store.keys()].filter(key => key.includes('usage')), [])
})

test('a response that arrives after the route or profile changed is discarded', async () => {
  for (const change of [env => { env.epoch += 1 }, env => env.profile.set('B')]) {
    const env = makeEnv({ profile: 'A' })
    env.run('plugin.register(ctx)')
    env.restImpl = async () => {
      change(env) // the user switches while the request is in flight
      return payloadFor([used('Session', 99)])
    }
    assert.equal(await env.run('fetchUsage()'), null)
    assert.deepEqual([...env.store.keys()].filter(key => key.includes('usage') || key.includes('alert')), [])
    assert.deepEqual(env.notifications, [])
    assert.deepEqual(env.osNotifications, [])
  }
})

test('the query key follows profile and epoch so a switch cannot reuse the previous data', () => {
  const env = makeEnv({ profile: 'A', epoch: 1 })
  const first = env.run('useUsage()').options.queryKey
  env.profile.set('B')
  const second = env.run('useUsage()').options.queryKey
  env.profile.set('A')
  env.epoch = 2
  const third = env.run('useUsage()').options.queryKey
  assert.equal(new Set([first, second, third].map(key => JSON.stringify(key))).size, 3)
  assert.ok(second.includes('B|'))
})

test('a host without the epoch export or profile routes still works', async () => {
  const env = makeEnv()
  env.set('sdk', {})
  env.run('delete host.profileRoutes')
  env.run('plugin.register(ctx)')
  env.restImpl = async () => payloadFor([used('Session', 29)])
  assert.equal((await env.run('fetchUsage()')).providers[0].windows[0].remaining_percent, 71)
})

test('the cache index keeps only a handful of profiles', async () => {
  const env = makeEnv()
  env.run('plugin.register(ctx)')
  env.restImpl = async () => payloadFor([used('Session', 29)])
  for (let i = 0; i < 12; i += 1) {
    env.profile.set(`p${i}`)
    await env.run('fetchUsage()')
  }
  assert.equal([...env.store.keys()].filter(key => key.startsWith('last-good-usage-v2:')).length, 8)
})

// --- threshold alerts ------------------------------------------------------------------------

test('alerts fire when a window crosses the warning and critical thresholds, once each', () => {
  const env = makeEnv()
  const evaluate = (percent, previous, extra) =>
    env.json(`evaluateAlerts(${JSON.stringify(payloadFor([used('Session', percent)], extra))}, ${JSON.stringify(previous)})`)
  let step = evaluate(50, {})
  assert.deepEqual(step.alerts, []) // first sighting only records the level
  step = evaluate(79, step.state)
  assert.deepEqual(step.alerts, [])
  step = evaluate(81, step.state)
  assert.equal(step.alerts.length, 1)
  assert.equal(step.alerts[0].level, 1)
  step = evaluate(85, step.state)
  assert.deepEqual(step.alerts, []) // still the same level: no repeat
  step = evaluate(96, step.state)
  assert.equal(step.alerts.length, 1)
  assert.equal(step.alerts[0].level, 2)
  step = evaluate(97, step.state)
  assert.deepEqual(step.alerts, [])
})

// U3: a window that is already critical the first time it is seen alerts once; a first reading at the
// warning level stays silent (no burst of warnings at start-up), and later readings are deduplicated.
test('a first reading that is already critical alerts once', () => {
  const env = makeEnv()
  const evaluate = (percent, previous) =>
    env.json(`evaluateAlerts(${JSON.stringify(payloadFor([used('Session', percent)]))}, ${JSON.stringify(previous)})`)
  let step = evaluate(96, {})
  assert.equal(step.alerts.length, 1)
  assert.equal(step.alerts[0].level, 2)
  step = evaluate(97, step.state)
  assert.deepEqual(step.alerts, []) // told once
  step = evaluate(99, step.state)
  assert.deepEqual(step.alerts, [])
  const warnFirst = evaluate(85, {})
  assert.deepEqual(warnFirst.alerts, []) // a first warning-level reading only records the level
  assert.equal(evaluate(96, warnFirst.state).alerts[0].level, 2)
})

test('a new window after a reset can alert again; a lower reading re-arms the level', () => {
  const env = makeEnv()
  const evaluate = (windows, previous) =>
    env.json(`evaluateAlerts(${JSON.stringify(payloadFor(windows))}, ${JSON.stringify(previous)})`)
  let step = evaluate([used('Session', 85)], {})
  step = evaluate([used('Session', 20)], step.state)
  step = evaluate([used('Session', 90)], step.state)
  assert.equal(step.alerts.length, 1)
  step = evaluate([used('Session', 90, { reset_at: '2026-11-06T20:00:00Z' })], step.state)
  assert.equal(step.alerts.length, 1) // same label, new reset: a new window
  const jitter = evaluate([used('Session', 90, { reset_at: '2026-11-06T20:00:20Z' })], step.state)
  assert.deepEqual(jitter.alerts, []) // seconds of jitter in the reset time are not a new window
})

// U4: the same logical reset reported a few seconds (or minutes) apart is the same window. Rounding to
// the minute flipped at :30, so 15:00:29 / 15:00:31 alternating re-alerted every time.
test('reset jitter within 5 minutes never re-arms an alert, across the minute boundary too', () => {
  const env = makeEnv()
  const evaluate = (reset, previous, percent = 90) =>
    env.json(`evaluateAlerts(${JSON.stringify(payloadFor([used('Session', percent, { reset_at: reset })]))}, ${JSON.stringify(previous)})`)
  let step = evaluate('2026-11-06T15:00:00Z', {}, 50)
  step = evaluate('2026-11-06T15:00:29Z', step.state)
  assert.equal(step.alerts.length, 1) // a real crossing
  for (const reset of ['2026-11-06T15:00:31Z', '2026-11-06T15:00:29Z', '2026-11-06T15:00:31Z',
    '2026-11-06T14:59:59Z', '2026-11-06T15:04:30Z', '2026-11-06T15:00:29Z']) {
    step = evaluate(reset, step.state)
    assert.deepEqual(step.alerts, [], reset)
  }
  const real = evaluate('2026-11-06T20:00:00Z', step.state)
  assert.equal(real.alerts.length, 1) // a real reset is a new window
  assert.equal(evaluate('2026-11-06T20:05:00Z', real.state).alerts.length, 0) // exactly 5 minutes: same window
  assert.equal(evaluate('2026-11-06T20:05:01Z', real.state).alerts.length, 1) // beyond 5 minutes: new window
  // State saved by 1.4.0 (reset rounded to the minute) still matches the same window.
  const legacy = { 'openai-codex::Session': { level: 1, reset: Math.round(Date.parse('2026-11-06T15:00:31Z') / 60000) } }
  assert.deepEqual(evaluate('2026-11-06T15:00:29Z', legacy).alerts, [])
})

test('alerts ignore stale, local, model-capped and disabled data and honor custom thresholds', () => {
  const env = makeEnv()
  const evaluate = (payload, previous) =>
    env.json(`evaluateAlerts(${JSON.stringify(payload)}, ${JSON.stringify(previous)})`)
  const seed = evaluate(payloadFor([used('Session', 10)]), {}).state
  for (const extra of [{ stale: true }, { local: true }, { usage_fallback: true }, { available: false }]) {
    assert.deepEqual(evaluate(payloadFor([used('Session', 99)], extra), seed).alerts, [])
  }
  assert.deepEqual(evaluate({ ...payloadFor([used('Session', 99)]), transport_stale: true }, seed).alerts, [])
  const capSeed = evaluate(payloadFor([used('Opus week', 10, { scope: 'model' })]), {}).state
  assert.deepEqual(evaluate(payloadFor([used('Opus week', 99, { scope: 'model' })]), capSeed).alerts, [])
  const off = payloadFor([used('Session', 99)])
  off.settings.alerts_enabled = false
  assert.deepEqual(evaluate(off, seed).alerts, [])
  const strict = payloadFor([used('Session', 55)])
  strict.settings = { alerts_enabled: true, alert_warn_percent: 50, alert_critical_percent: 60 }
  const strictSeed = evaluate({ ...strict, providers: payloadFor([used('Session', 10)]).providers }, {}).state
  assert.equal(evaluate(strict, strictSeed).alerts[0].level, 1)
  const broken = payloadFor([used('Session', 85)])
  broken.settings = { alerts_enabled: true, alert_warn_percent: 90, alert_critical_percent: 70 }
  assert.deepEqual(evaluate(broken, seed).alerts.map(alert => alert.level), [1]) // invalid pair falls back to 80/95
})

test('crossing a threshold shows an in-app toast and a native notification, remembered per profile', async () => {
  const env = makeEnv({ profile: 'A' })
  env.run('plugin.register(ctx)')
  env.restImpl = async () => payloadFor([used('Session', 50)])
  await env.run('fetchUsage()')
  assert.deepEqual(env.notifications, [])
  env.restImpl = async () => payloadFor([used('Session', 96)])
  await env.run('fetchUsage()')
  assert.equal(env.notifications.length, 1)
  assert.equal(env.notifications[0].kind, 'warning')
  assert.match(env.notifications[0].title, /OpenAI Codex.*almost exhausted/)
  assert.match(env.osNotifications[0].body, /Session: 96% used/)
  await env.run('fetchUsage()')
  assert.equal(env.osNotifications.length, 1) // already told
  env.profile.set('B') // another profile has its own alert memory
  env.restImpl = async () => payloadFor([used('Session', 96)])
  await env.run('fetchUsage()')
  assert.equal(env.osNotifications.length, 2) // already critical when B first sees it: told once (U3)
  await env.run('fetchUsage()')
  assert.equal(env.osNotifications.length, 2)
  assert.ok(env.store.has('alert-state-v1:A|'))
  assert.ok(env.store.has('alert-state-v1:B|'))
})

// U7: two sources expose the same profile name (local + SSH), so nothing is persisted and the alert memory
// lives in memory. Switching source bumps the activation epoch; the other source's levels must not leak.
test('in-memory alert state of two homonymous sources is kept apart by the epoch', async () => {
  const routes = [{ profile: 'main', connectionId: 'local-1' }, { profile: 'main', connectionId: 'ssh-9' }]
  const env = makeEnv({ profile: 'main', routes, epoch: 1 })
  env.run('plugin.register(ctx)')
  env.restImpl = async () => payloadFor([used('Session', 50)])
  await env.run('fetchUsage()') // source A: 50 %
  env.epoch = 2 // the user switches to source B, another account
  env.restImpl = async () => payloadFor([used('Session', 85)])
  await env.run('fetchUsage()')
  assert.equal(env.osNotifications.length, 0) // B was first seen at 85 %: no crossing happened on B
  env.restImpl = async () => payloadFor([used('Session', 96)])
  await env.run('fetchUsage()') // B goes critical
  assert.equal(env.osNotifications.length, 1)
  env.epoch = 3 // back to A, already critical on its own: A must still be told once
  env.restImpl = async () => payloadFor([used('Session', 97)])
  await env.run('fetchUsage()')
  assert.equal(env.osNotifications.length, 2)
  assert.equal(env.run('memoryAlertState.size'), 1) // entries of earlier epochs are dropped
  assert.deepEqual([...env.store.keys()].filter(key => key.includes('alert')), []) // still nothing persisted
})

// N3: documented on purpose (USER_GUIDE "Alerts", PRIVACY). Two sources sharing a profile name cannot
// be told apart safely, so their alert memory lives one activation (epoch) at a time: returning to a
// source that is still critical announces it again, once per activation, never twice in the same one.
test('a critical source is announced again once per activation when homonymous sources alternate', async () => {
  const routes = [{ profile: 'main', connectionId: 'local-1' }, { profile: 'main', connectionId: 'ssh-9' }]
  const env = makeEnv({ profile: 'main', routes, epoch: 1 })
  env.run('plugin.register(ctx)')
  const sourceA = async () => payloadFor([used('Session', 97)]) // same percentage and reset throughout
  const sourceB = async () => payloadFor([used('Session', 50)])
  env.restImpl = sourceA
  await env.run('fetchUsage()') // A critical, first sighting
  await env.run('fetchUsage()') // same activation, unchanged: no repeat
  assert.equal(env.osNotifications.length, 1)
  env.epoch = 2
  env.restImpl = sourceB
  await env.run('fetchUsage()') // B healthy: silent
  assert.equal(env.osNotifications.length, 1)
  env.epoch = 3
  env.restImpl = sourceA
  await env.run('fetchUsage()') // back to A, nothing changed on A: announced again (new activation)
  await env.run('fetchUsage()') // ...but only once in that activation
  assert.equal(env.osNotifications.length, 2)
  assert.equal(env.notifications.length, 2)
  assert.match(env.osNotifications[1].body, /Session: 97(\.0)?% used/)
})

test('notifications carry only the provider, window, percentage and reset time', async () => {
  const env = makeEnv()
  env.run('plugin.register(ctx)')
  env.restImpl = async () => payloadFor([used('Session', 50)])
  await env.run('fetchUsage()')
  env.restImpl = async () => payloadFor([used('Session', 99)], { details: ['Authorization: Bearer abc'] })
  await env.run('fetchUsage()')
  assert.equal(env.osNotifications.length, 1)
  const text = JSON.stringify([env.notifications, env.osNotifications]).toLowerCase()
  assert.ok(!text.includes('bearer'))
  assert.ok(!text.includes('authorization'))
})
