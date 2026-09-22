const assert = require('node:assert/strict')
const { test } = require('node:test')
const { readFileSync } = require('node:fs')
const vm = require('node:vm')
const path = require('node:path')

function loadPlugin(data, saved = '__auto_session__', provider = 'anthropic') {
  const atom = initial => ({ value: initial, get() { return this.value }, set(value) { this.value = value } })
  const jsx = (type, props) => ({ type, props })
  const context = vm.createContext({
    atom, jsx, jsxs: jsx, Button: 'button', cn: (...values) => values.filter(Boolean).join(' '),
    useState: value => [value, () => {}], useValue: value => value.get(),
    useQuery: () => ({ data }), queryClient: { getQueryData: () => null },
    STATUSBAR_AREAS: { right: 'right' },
    host: { state: { focusedSessionId: atom('s'), activeSessionId: atom('s'), model: atom('claude') }, onEvent() {} }
  })
  const source = readFileSync(path.join(__dirname, '../desktop/plugin.js'), 'utf8')
    .replace(/import\s+[\s\S]*?from\s+['"][^'"]+['"]\s*/g, '')
    .replace('export default', 'const plugin =')
  vm.runInContext(source, context)
  context.input = { data, saved, provider }
  vm.runInContext(`$statusSelection.set(input.saved); $runtimeInfoBySession.set({s: {provider: input.provider}})`, context)
  return expression => vm.runInContext(expression, context)
}
const window = (label, remaining_percent) => ({ label, remaining_percent })
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
  assert.equal(loadPlugin(data)('UsageStatusLabel()').props.children, 'AI 0.0%')
})

test('weekly then monthly fallback recognizes EN and ES labels', () => {
  for (const weekly of ['Weekly', 'week', 'semana']) {
    for (const monthly of ['Monthly', 'month', 'mensual', 'Subscription period']) {
      const data = { providers: [provider('anthropic', [window(monthly, 1), window(weekly, 95)])] }
      assert.equal(select(data).selected.window, weekly)
      data.providers[0].windows = [window('Other quota', 0), window(monthly, 95)]
      assert.equal(select(data).selected.window, monthly)
    }
  }
})

test('active provider isolation, unsupported provider, empty data and stable ties', () => {
  const data = { providers: [
    provider('openai-codex', [window('Session', 1)]),
    provider('anthropic', [window('Session', 94), window('Current session', 2), window('Weekly', 0)])
  ] }
  assert.equal(select(data).selected.value, 'anthropic::Session')
  assert.equal(select(data, '__auto_session__', 'openai-codex').selected.remaining, 1)
  assert.equal(select(data, '__auto_session__', 'unsupported').selected, null)
  assert.equal(select(data, 'missing::Weekly', 'unsupported').selected, null)
  assert.equal(select(data, 'missing::Weekly').selected.value, 'anthropic::Session')
  assert.equal(select(data, 'missing::Weekly').fellBack, true)
  assert.equal(select(undefined).selected, null)
  assert.equal(select({ providers: [] }).selected, null)
  assert.equal(select(data, 'anthropic::Weekly', 'unsupported').selected.value, 'anthropic::Session')
})

test('saved and clicked weekly pin the provider, not its period', () => {
  const data = { providers: [
    provider('anthropic', [window('Weekly', 92), window('Session', 94)]),
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
  assert.ok(panel.some(node => /session.*week.*month/i.test(node.props?.children)))
})

test('automatic chooses session before weekly regardless of remaining or input order', () => {
  for (const label of ['Session', 'Current session', '5-hour window', '5 horas', 'sesión']) {
    for (const [session, weekly] of [[94, 92], [92, 94]]) {
      for (const windows of [[window('Weekly', weekly), window(label, session)], [window(label, session), window('Weekly', weekly)]]) {
        const data = { providers: [provider('anthropic', windows)] }
        const before = JSON.stringify(data)
        const selected = select(data).selected
        assert.equal(selected.window, label)
        assert.equal(selected.remaining, session)
        assert.equal(JSON.stringify(data), before)
      }
    }
  }
})
