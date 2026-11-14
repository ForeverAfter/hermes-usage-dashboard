import {
  atom,
  Button,
  cn,
  host,
  queryClient,
  STATUSBAR_AREAS,
  useQuery,
  useValue
} from '@hermes/plugin-sdk'
import * as sdk from '@hermes/plugin-sdk'
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'usage-dashboard'
const LEGACY_USAGE_CACHE_KEY = 'last-good-usage' // unscoped (pre-1.4): may belong to another profile
const USAGE_CACHE_KEY = 'last-good-usage-v2'
const CACHE_INDEX_KEY = 'usage-cache-index-v2'
const ALERT_STATE_KEY = 'alert-state-v1'
const MAX_SCOPES_KEPT = 8
const USAGE_CACHE_MAX_AGE_MS = 24 * 60 * 60 * 1000
const AUTO_SELECTION = '__auto_session__'
const SELECTION_KEY = 'statusbar-selection-v2'
const DEFAULT_ALERTS = { enabled: true, warn: 80, critical: 95 }
const $statusSelection = atom(AUTO_SELECTION)
const $runtimeInfoBySession = atom({})
const $noValue = atom('')
const memoryAlertState = new Map()
let pluginCtx = null

// ctx.storage is app-level: one root for every profile, gateway and remote machine. Everything the
// plugin keeps there (and every query it caches) is keyed by the profile and connection it came from,
// and a response is dropped if the active route changed while it was in flight.
function activationEpoch() {
  return typeof sdk.gatewayActivationEpoch === 'function' ? sdk.gatewayActivationEpoch() : 0
}

function readUsageScope() {
  const profile = String(host.state.profile?.get?.() || 'default')
  let connection = ''
  let persist = true
  try {
    const routes = typeof host.profileRoutes === 'function' ? host.profileRoutes() || [] : []
    const same = routes.filter(route => route && route.profile === profile)
    if (same.length === 1 && same[0].connectionId) connection = String(same[0].connectionId)
    // Two sources expose this profile name and none is marked active: keep the data in memory only.
    else if (same.length > 1) persist = false
  } catch {
    persist = false
  }
  const epoch = activationEpoch()
  const key = `${profile}|${connection}`
  return { key, persist, epoch, queryKey: [ID, 'usage', key, epoch] }
}

function useUsageScope() {
  useValue(host.state.profile || $noValue) // re-render when the profile or the gateway route changes
  useValue(host.state.gateway || $noValue)
  return readUsageScope()
}

function scopeIsCurrent(scope) {
  const now = readUsageScope()
  return now.key === scope.key && now.epoch === scope.epoch
}

function clamp(value) {
  return Math.max(0, Math.min(100, Number(value) || 0))
}

function formatPercent(value) {
  const normalized = clamp(value)
  return `${normalized.toFixed(normalized >= 10 ? 0 : 1)}%`
}

function formatDate(value) {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return date.toLocaleString('en-US', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit'
  })
}

function selectableWindows(data) {
  return (data?.providers || []).flatMap(provider =>
    (provider.windows || [])
      .map(window => ({
        value: `${provider.provider}::${window.label}`,
        providerId: provider.provider,
        provider: provider.label,
        window: window.label,
        scope: window.scope === 'model' ? 'model' : 'account',
        remaining: window.remaining_percent == null ? null : clamp(window.remaining_percent),
        reset_at: typeof window.reset_at === 'string' ? window.reset_at : null
      }))
  )
}

const MODEL_FAMILIES = ['opus', 'sonnet']

// A model-scoped window (Opus week, Sonnet week) caps one model family, never the account: it only
// speaks for the status bar when the session is actually running that family.
function modelWindowApplies(windowLabel, model) {
  const label = String(windowLabel || '').toLowerCase()
  const current = String(model || '').toLowerCase()
  return MODEL_FAMILIES.some(family => label.includes(family) && current.includes(family))
}

function eligibleOptions(options, runtime) {
  return options.filter(option => option.scope !== 'model' || modelWindowApplies(option.window, runtime?.model))
}

function usageProviderId(provider, model) {
  const providerName = String(provider || '').toLowerCase()
  if (providerName.includes('openai') || providerName.includes('codex')) return 'openai-codex'
  if (providerName.includes('anthropic') || providerName.includes('claude')) return 'anthropic'
  if (providerName.includes('kimi') || providerName.includes('moonshot')) return 'kimi'
  if (providerName.includes('z.ai') || providerName.includes('z-ai') || providerName === 'zai') return 'zai'
  if (providerName.includes('nous')) return 'nous'
  if (providerName) return null

  const modelName = String(model || '').toLowerCase()
  if (/(^|[\s/])gpt[-_]/.test(modelName) || modelName.includes('codex')) return 'openai-codex'
  if (modelName.includes('claude')) return 'anthropic'
  if (modelName.includes('kimi') || modelName.includes('moonshot')) return 'kimi'
  if (modelName.includes('glm')) return 'zai'
  if (modelName.includes('hermes')) return 'nous'
  return null
}

function useActiveRuntime() {
  const focusedSessionId = useValue(host.state.focusedSessionId)
  const activeSessionId = useValue(host.state.activeSessionId)
  const fallbackModel = useValue(host.state.model)
  const infoBySession = useValue($runtimeInfoBySession)
  const sessionId = focusedSessionId || activeSessionId
  const info = (sessionId && infoBySession[sessionId]) || {}
  const model = info.model || fallbackModel || ''
  const provider = info.provider || ''
  return {
    sessionId,
    model,
    provider,
    usageProvider: usageProviderId(provider, model)
  }
}

function windowPriority(label) {
  const name = String(label || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '')
  if (/session|sesion|5\s*[- ]?\s*(?:hour|hora|h\b)/.test(name)) return 0
  if (/week|semana/.test(name)) return 1
  if (/month|mensual|subscription period/.test(name)) return 2
  return 3
}

// Same threshold as the backend's availability.json: 1 % remaining or less is exhausted.
const EXHAUSTED_AT_OR_BELOW = 1

function isExhausted(option) {
  return option?.remaining != null && option.remaining <= EXHAUSTED_AT_OR_BELOW
}

function resetTime(option) {
  const time = option.reset_at ? Date.parse(option.reset_at) : NaN
  return Number.isFinite(time) ? time : null
}

// Of two exhausted windows, the one that blocks longer: an unknown reset outranks a known one, a later
// reset an earlier one, and on a tie the session wins (as in the backend's availability.json).
function blocksLonger(option, current) {
  const a = resetTime(option)
  const b = resetTime(current)
  if ((a == null) !== (b == null)) return a == null
  if (a != null && a !== b) return a > b
  return windowPriority(option.window) < windowPriority(current.window)
}

// The window that speaks for a provider. It mirrors the backend's availability policy so the chip
// never contradicts availability.json: the primary windows are session and week (any account window
// when the provider has neither); an exhausted one comes first, the one that blocks longest; otherwise
// the lowest remaining, earlier input order on ties. A primary window without a percentage keeps the
// chip at "no data" rather than letting another window speak for it. The cap of the model in use
// (Opus / Sonnet week) wins only when it is stricter.
function priorityOption(options) {
  const account = options.filter(option => option.scope !== 'model')
  const caps = options.filter(option => option.scope === 'model')
  const sessionOrWeek = account.filter(option => windowPriority(option.window) <= 1)
  const primary = sessionOrWeek.length ? sessionOrWeek : account
  const known = primary.filter(option => option.remaining != null)
  const exhausted = known.filter(isExhausted)
  let chosen = null
  if (exhausted.length) {
    chosen = exhausted.reduce((current, option) => (blocksLonger(option, current) ? option : current))
  } else if (known.length < primary.length) {
    chosen = primary
      .filter(option => option.remaining == null)
      .reduce((current, option) =>
        windowPriority(option.window) < windowPriority(current.window) ? option : current)
  } else if (known.length) {
    chosen = known.reduce((current, option) => {
      if (option.remaining !== current.remaining) return option.remaining < current.remaining ? option : current
      return windowPriority(option.window) < windowPriority(current.window) ? option : current
    })
  }
  const cap = caps
    .filter(option => option.remaining != null)
    .reduce((current, option) => (!current || option.remaining < current.remaining ? option : current), null)
  if (cap) {
    if (!chosen) chosen = cap
    else if (chosen.remaining == null ? isExhausted(cap) : cap.remaining < chosen.remaining) chosen = cap
  }
  if (!chosen && caps.length) chosen = caps[0]
  return chosen ? { ...chosen, exhausted: isExhausted(chosen) } : null
}

function selectedStatus(data, selection, runtime) {
  const options = eligibleOptions(selectableWindows(data), runtime)
  const automaticOptions = runtime?.usageProvider
    ? options.filter(option => option.providerId === runtime.usageProvider)
    : []
  const automatic = priorityOption(automaticOptions)

  if (selection === AUTO_SELECTION) {
    return { selected: automatic, fellBack: false, automatic: true }
  }

  const pinnedProvider = typeof selection === 'string' ? selection.split('::')[0] : null
  const explicit = priorityOption(options.filter(option => option.providerId === pinnedProvider))
  return {
    selected: explicit || automatic,
    fellBack: !explicit,
    automatic: false
  }
}

function persistSelection(value) {
  $statusSelection.set(value)
  pluginCtx?.storage.set(SELECTION_KEY, value)
}

function sanitizeWindow(window) {
  return {
    label: typeof window?.label === 'string' ? window.label : '',
    used_percent: Number.isFinite(window?.used_percent) ? window.used_percent : null,
    remaining_percent: Number.isFinite(window?.remaining_percent) ? window.remaining_percent : null,
    reset_at: typeof window?.reset_at === 'string' ? window.reset_at : null,
    detail: typeof window?.detail === 'string' ? window.detail : null,
    scope: window?.scope === 'model' ? 'model' : 'account'
  }
}

function sanitizeSettings(settings) {
  const percent = (value, fallback) =>
    Number.isFinite(value) && value >= 1 && value <= 100 ? value : fallback
  const warn = percent(settings?.alert_warn_percent, DEFAULT_ALERTS.warn)
  const critical = percent(settings?.alert_critical_percent, DEFAULT_ALERTS.critical)
  return {
    alerts_enabled: settings?.alerts_enabled !== false,
    alert_warn_percent: critical > warn ? warn : DEFAULT_ALERTS.warn,
    alert_critical_percent: critical > warn ? critical : DEFAULT_ALERTS.critical
  }
}

function sanitizeProvider(provider) {
  return {
    provider: typeof provider?.provider === 'string' ? provider.provider : '',
    label: typeof provider?.label === 'string' ? provider.label : '',
    available: Boolean(provider?.available),
    plan: typeof provider?.plan === 'string' ? provider.plan : null,
    source: typeof provider?.source === 'string' ? provider.source : 'local',
    fetched_at: typeof provider?.fetched_at === 'string' ? provider.fetched_at : null,
    renewal_at: typeof provider?.renewal_at === 'string' ? provider.renewal_at : null,
    stale: Boolean(provider?.stale),
    local: Boolean(provider?.local),
    usage_fallback: Boolean(provider?.usage_fallback),
    usage_fallback_reason:
      typeof provider?.usage_fallback_reason === 'string' ? provider.usage_fallback_reason : null,
    windows: Array.isArray(provider?.windows) ? provider.windows.map(sanitizeWindow) : [],
    details: Array.isArray(provider?.details) ? provider.details.filter(detail => typeof detail === 'string') : [],
    reason: typeof provider?.reason === 'string' ? provider.reason : null
  }
}

function sanitizedUsage(payload) {
  return JSON.parse(JSON.stringify({
    fetched_at: payload?.fetched_at || null,
    summary: payload?.summary || null,
    settings: sanitizeSettings(payload?.settings),
    providers: Array.isArray(payload?.providers) ? payload.providers.map(sanitizeProvider) : [],
    privacy: typeof payload?.privacy === 'string' ? payload.privacy : null
  }))
}

function mergeProviderFallback(payload, previous) {
  if (!previous?.providers?.length) return payload
  const priorByProvider = new Map(previous.providers.map((provider) => [provider.provider, provider]))
  const providers = payload.providers.map((provider) => {
    if (provider.available) return provider
    const prior = priorByProvider.get(provider.provider)
    if (!prior?.available) return provider
    const previousReason = prior.usage_fallback_reason
    const transientReasons = new Set([
      previousReason,
      provider.reason,
      'No compatible session was found, or the provider did not return usage limits.'
    ].filter(Boolean))
    const baseDetails = (prior.details || []).filter((detail) => !transientReasons.has(detail))
    const reason = `${provider.label || provider.provider} did not return fresh usage data; showing the last valid value.`
    return {
      ...JSON.parse(JSON.stringify(prior)),
      stale: true,
      usage_fallback: true,
      usage_fallback_reason: reason,
      details: [...new Set([...baseDetails, reason])]
    }
  })
  const remaining = providers.flatMap((provider) =>
    provider.available
      ? (provider.windows || [])
          .filter((window) => window.scope !== 'model')
          .map((window) => window.remaining_percent)
          .filter(Number.isFinite)
      : []
  )
  return {
    ...payload,
    providers,
    summary: {
      ...(payload.summary || {}),
      available: providers.filter((provider) => provider.available).length,
      total: providers.length,
      lowest_remaining_percent: remaining.length ? Math.min(...remaining) : null
    }
  }
}

// Persisted last-good payloads, one entry per profile+connection. A small index bounds how many.
function storageKey(base, scope) {
  return `${base}:${scope.key}`
}

function rememberScope(scope) {
  const index = pluginCtx.storage.get(CACHE_INDEX_KEY, [])
  const known = Array.isArray(index) ? index.filter(key => key !== scope.key) : []
  known.push(scope.key)
  while (known.length > MAX_SCOPES_KEPT) {
    const dropped = known.shift()
    pluginCtx.storage.remove(`${USAGE_CACHE_KEY}:${dropped}`)
    pluginCtx.storage.remove(`${ALERT_STATE_KEY}:${dropped}`)
  }
  pluginCtx.storage.set(CACHE_INDEX_KEY, known)
}

function saveUsageCache(payload, scope) {
  if (!scope.persist || payload?.providers?.some((provider) => provider.usage_fallback)) return
  rememberScope(scope)
  pluginCtx.storage.set(storageKey(USAGE_CACHE_KEY, scope), {
    saved_at: Date.now(),
    payload: sanitizedUsage(payload)
  })
}

function loadUsageCache(scope) {
  if (!scope.persist) return null
  const key = storageKey(USAGE_CACHE_KEY, scope)
  const entry = pluginCtx.storage.get(key, null)
  if (!entry?.payload || !Number.isFinite(entry.saved_at)) return null
  if (Date.now() - entry.saved_at > USAGE_CACHE_MAX_AGE_MS) {
    pluginCtx.storage.remove(key)
    return null
  }
  return sanitizedUsage(entry.payload)
}

// --- threshold alerts -------------------------------------------------------------------------

function alertSettings(payload) {
  const settings = sanitizeSettings(payload?.settings)
  return {
    enabled: settings.alerts_enabled,
    warn: settings.alert_warn_percent,
    critical: settings.alert_critical_percent
  }
}

function alertLevel(used, settings) {
  if (used >= settings.critical) return 2
  if (used >= settings.warn) return 1
  return 0
}

// A window is identified by provider, label and reset minute (so jitter in the timestamp does not
// re-arm it). Crossing means seeing the level go up. The first sighting only records the level, except
// that a window already critical when first seen alerts once (otherwise it would never be announced);
// a first reading at the warning level stays silent so start-up does not raise a burst of warnings.
function evaluateAlerts(payload, previous) {
  const settings = alertSettings(payload)
  const next = { ...(previous || {}) }
  const alerts = []
  if (!settings.enabled || payload?.transport_stale) return { alerts, state: next }
  for (const provider of payload?.providers || []) {
    if (!provider.available || provider.stale || provider.usage_fallback || provider.local) continue
    for (const window of provider.windows || []) {
      if (window.scope === 'model' || !Number.isFinite(window.used_percent)) continue
      const resetMinute = window.reset_at ? Math.round(Date.parse(window.reset_at) / 60000) : 0
      const key = `${provider.provider}::${window.label}`
      const level = alertLevel(window.used_percent, settings)
      const before = next[key]
      next[key] = { level, reset: Number.isFinite(resetMinute) ? resetMinute : 0 }
      const announce = before
        ? level > 0 && (before.reset !== next[key].reset || level > before.level)
        : level >= 2
      if (announce) {
        alerts.push({
          level,
          provider: provider.label || provider.provider,
          window: window.label,
          used: window.used_percent,
          reset_at: window.reset_at
        })
      }
    }
  }
  return { alerts, state: next }
}

function showAlerts(alerts) {
  for (const alert of alerts) {
    const critical = alert.level >= 2
    const title = `${alert.provider}: ${critical ? 'quota almost exhausted' : 'quota warning'}`
    const reset = formatDate(alert.reset_at)
    const body = `${alert.window}: ${formatPercent(alert.used)} used${reset ? ` · resets ${reset}` : ''}`
    try {
      host.notify({ kind: critical ? 'warning' : 'info', title, message: body })
    } catch {}
    try {
      // Native notification; Hermes shows it only while the app is in the background.
      pluginCtx?.os?.notify?.({ title, body })
    } catch {}
  }
}

function runAlerts(payload, scope) {
  const persisted = scope.persist
    ? pluginCtx.storage.get(storageKey(ALERT_STATE_KEY, scope), null)
    : memoryAlertState.get(scope.key)
  const { alerts, state } = evaluateAlerts(payload, persisted || {})
  if (scope.persist) {
    rememberScope(scope)
    pluginCtx.storage.set(storageKey(ALERT_STATE_KEY, scope), state)
  } else {
    memoryAlertState.set(scope.key, state)
  }
  showAlerts(alerts)
}

// Returns null when the active profile or route changed while the request was in flight: that
// response belongs to a scope nobody is looking at any more and must not be shown, cached or alerted.
async function fetchUsage(scope = readUsageScope(), path = '/usage') {
  if (!pluginCtx) throw new Error('Usage plugin is not initialized')
  try {
    const payload = await pluginCtx.rest(path, { timeoutMs: 35000 })
    if (!scopeIsCurrent(scope)) return null
    const clean = sanitizedUsage(payload)
    const previous = queryClient.getQueryData(scope.queryKey) || loadUsageCache(scope)
    const fresh = { ...mergeProviderFallback(clean, previous), transport_stale: false }
    saveUsageCache(fresh, scope)
    try {
      runAlerts(fresh, scope)
    } catch {}
    return fresh
  } catch (error) {
    if (!scopeIsCurrent(scope)) return null
    const memoryCache = queryClient.getQueryData(scope.queryKey)
    const storedCache = loadUsageCache(scope)
    const cached = memoryCache?.providers?.length ? sanitizedUsage(memoryCache) : storedCache
    if (cached?.providers?.length) {
      return {
        ...cached,
        transport_stale: true,
        transport_reason: 'The active profile does not currently expose the plugin backend.'
      }
    }
    throw error
  }
}

function useUsage() {
  const scope = useUsageScope()
  return useQuery({
    queryKey: scope.queryKey,
    queryFn: () => fetchUsage(scope),
    refetchInterval: 60000,
    staleTime: 30000,
    retry: 1
  })
}

function ProgressBar({ used, label }) {
  const value = clamp(used)
  return jsx('div', {
    className: 'h-1 overflow-hidden rounded-full bg-(--ui-stroke-secondary)',
    role: 'progressbar',
    'aria-label': label,
    'aria-valuemin': 0,
    'aria-valuemax': 100,
    'aria-valuenow': value,
    children: jsx('div', {
      className: 'h-full rounded-full transition-[width] duration-300',
      style: { width: `${value}%`, background: 'var(--ui-accent)' }
    })
  })
}

function CompactWindow({ provider, window, selectedValue }) {
  const reset = formatDate(window.reset_at)
  const selectionValue = `${provider.provider}::${window.label}`
  const selected = selectedValue === selectionValue

  function chooseForStatus(event) {
    event.preventDefault()
    event.stopPropagation()
    persistSelection(selectionValue)
  }

  return jsxs('div', {
    className: 'space-y-1.5',
    children: [
      jsxs('div', {
        className: 'flex items-baseline justify-between gap-3 text-xs',
        children: [
          jsx('span', {
            className: 'min-w-0 truncate text-(--ui-text-secondary)',
            title: window.scope === 'model' ? 'Caps one model family only; it does not mean the account is out of quota.' : undefined,
            children: window.scope === 'model' ? `${window.label} (model cap)` : window.label
          }),
          window.remaining_percent == null
            ? jsx('span', { className: 'shrink-0 text-(--ui-text-quaternary)', children: 'No percentage' })
            : jsx('button', {
                className: cn(
                  'shrink-0 rounded px-1 py-0.5 font-semibold tabular-nums transition-colors hover:bg-(--chrome-action-hover)',
                  selected ? 'text-(--ui-accent)' : 'text-foreground'
                ),
                type: 'button',
                'aria-pressed': selected,
                title: 'Pin this provider to the status bar (it shows an exhausted window first, otherwise the lower of session and weekly)',
                onClick: chooseForStatus,
                children: `${selected ? '● ' : ''}${formatPercent(window.remaining_percent)} remaining`
              })
        ]
      }),
      window.used_percent == null
        ? null
        : jsx(ProgressBar, { used: window.used_percent, label: `${provider.label} — ${window.label}` }),
      reset || window.detail
        ? jsxs('div', {
            className: 'flex flex-wrap justify-between gap-x-3 gap-y-0.5 text-[0.6875rem] leading-relaxed text-(--ui-text-quaternary)',
            children: [
              window.detail ? jsx('span', { children: window.detail }) : null,
              reset ? jsx('span', { className: 'shrink-0', children: `Resets ${reset}` }) : null
            ]
          })
        : null
    ]
  })
}

function CompactProvider({ provider, selectedValue }) {
  const fetched = formatDate(provider.fetched_at)
  const renewal = formatDate(provider.renewal_at)
  const badge = provider.usage_fallback || provider.stale
    ? 'STALE'
    : provider.local
      ? 'LOCAL'
      : provider.available
        ? 'ACTIVE'
        : 'NO DATA'

  return jsxs('section', {
    className: cn('space-y-2.5 border-b border-(--ui-stroke-secondary) px-3 py-3 last:border-b-0', !provider.available && 'opacity-70'),
    children: [
      jsxs('div', {
        className: 'flex items-start justify-between gap-3',
        children: [
          jsxs('div', {
            className: 'min-w-0',
            children: [
              jsx('div', { className: 'truncate text-xs font-semibold', children: provider.label }),
              jsx('div', {
                className: 'truncate text-[0.6875rem] text-(--ui-text-tertiary)',
                children: provider.plan || 'Plan not identified'
              }),
              renewal
                ? jsx('div', {
                    className: 'truncate text-[0.625rem] text-(--ui-text-quaternary)',
                    children: `Plan renewal / cutoff ${renewal}`
                  })
                : null
            ]
          }),
          jsx('span', {
            className: cn(
              'shrink-0 rounded-full border border-(--ui-stroke-secondary) px-1.5 py-0.5 text-[0.5625rem] font-medium',
              provider.available && !provider.stale && !provider.local
                ? 'text-(--ui-accent)'
                : 'text-(--ui-text-quaternary)'
            ),
            children: badge
          })
        ]
      }),
      provider.available && provider.windows?.length
        ? jsx('div', {
            className: 'space-y-2.5',
            children: provider.windows.map((window, index) =>
              jsx(CompactWindow, { provider, window, selectedValue }, `${provider.provider}-${index}`)
            )
          })
        : jsx('div', {
            className: 'text-[0.6875rem] leading-relaxed text-(--ui-text-tertiary)',
            children: provider.available
              ? 'Balance available in the details below.'
              : provider.reason || 'The provider did not return usage information.'
          }),
      provider.details?.length
        ? jsx('div', {
            className: 'space-y-0.5 text-[0.6875rem] leading-relaxed text-(--ui-text-tertiary)',
            children: provider.details.map((detail, index) => jsx('div', { children: detail }, index))
          })
        : null,
      jsx('div', {
        className: 'truncate text-[0.5625rem] text-(--ui-text-quaternary)',
        title: provider.source,
        children: `${provider.source}${fetched ? ` · ${fetched}` : ''}`
      })
    ]
  })
}

function UsageCompactPanel() {
  const query = useUsage()
  const selection = useValue($statusSelection)
  const runtime = useActiveRuntime()
  const [refreshing, setRefreshing] = useState(false)
  const data = query.data
  const { selected: selectedForBar, fellBack } = selectedStatus(data, selection, runtime)

  async function forceRefresh(event) {
    event?.preventDefault?.()
    event?.stopPropagation?.()
    setRefreshing(true)
    try {
      const scope = readUsageScope()
      const payload = await fetchUsage(scope)
      if (!payload) return // the profile or route changed while loading: nothing to show here
      queryClient.setQueryData(scope.queryKey, payload)
      if (payload.transport_stale) {
        host.notify({ kind: 'info', message: 'Showing the last saved usage data; this profile has not loaded the backend yet.' })
      }
    } catch {
      host.notify({ kind: 'error', message: 'Usage data could not be refreshed.' })
    } finally {
      setRefreshing(false)
    }
  }

  return jsxs('div', {
    className: 'flex min-h-40 flex-col',
    style: { maxHeight: '70vh' },
    onKeyDown: event => event.stopPropagation(),
    children: [
      jsxs('header', {
        className: 'flex shrink-0 items-center justify-between gap-3 border-b border-(--ui-stroke-secondary) px-3 py-2.5',
        children: [
          jsxs('div', {
            className: 'min-w-0',
            children: [
              jsx('div', { className: 'text-xs font-semibold', children: 'AI Usage' }),
              jsx('div', {
                className: 'truncate text-[0.6875rem] text-(--ui-text-tertiary)',
                'aria-live': 'polite',
                children: data?.summary
                  ? `${data.transport_stale ? 'Cached data · ' : ''}${data.summary.available} of ${data.summary.total} providers available`
                  : 'Loading providers…'
              })
            ]
          }),
          jsx(Button, {
            size: 'sm',
            variant: 'ghost',
            disabled: refreshing || query.isPending,
            onClick: forceRefresh,
            children: refreshing ? 'Refreshing…' : 'Refresh'
          })
        ]
      }),
      jsx('div', {
        className: 'shrink-0 border-b border-(--ui-stroke-secondary) px-3 py-2',
        children: jsxs('div', {
          className: 'space-y-1 text-[0.6875rem]',
          children: [
            jsxs('div', {
              className: 'flex items-center justify-between gap-3',
              children: [
                jsx('span', { className: 'shrink-0 text-(--ui-text-tertiary)', children: 'Status bar display' }),
                jsxs('div', {
                  className: 'flex min-w-0 items-center gap-2',
                  children: [
                    jsx('span', {
                      className: 'truncate font-medium text-foreground',
                      title: selectedForBar
                        ? `${selectedForBar.provider} · ${selectedForBar.window}`
                        : runtime.provider || runtime.model || undefined,
                      children: selection === AUTO_SELECTION
                        ? `Automatic · ${selectedForBar?.provider || runtime.provider || 'no linked quota'}`
                        : selectedForBar
                          ? `Pinned provider · ${selectedForBar.provider} · ${selectedForBar.window}${fellBack ? ' (fallback)' : ''}`
                          : 'Pinned provider · data unavailable'
                    }),
                    selection !== AUTO_SELECTION
                      ? jsx('button', {
                          className: 'shrink-0 rounded px-1.5 py-0.5 text-(--ui-accent) hover:bg-(--chrome-action-hover)',
                          type: 'button',
                          onClick: event => {
                            event.preventDefault()
                            event.stopPropagation()
                            persistSelection(AUTO_SELECTION)
                          },
                          children: 'Follow session'
                        })
                      : null
                  ]
                })
              ]
            }),
            jsx('div', {
              className: 'truncate text-[0.625rem] text-(--ui-text-quaternary)',
              title: runtime.model || undefined,
              children: selection === AUTO_SELECTION
                ? `Current model: ${runtime.model || 'unknown'}`
                : 'Click any percentage to pin its provider: exhausted window first, otherwise the lower of session and weekly.'
            })
          ]
        })
      }),
      query.isPending
        ? jsx('div', {
            className: 'flex flex-1 items-center justify-center px-4 py-10 text-xs text-(--ui-text-tertiary)',
            children: 'Loading usage limits…'
          })
        : query.isError && !data
          ? jsxs('div', {
              className: 'flex flex-1 flex-col items-center justify-center gap-2 px-5 py-8 text-center',
              children: [
                jsx('div', { className: 'text-xs font-medium', children: 'Usage data could not be loaded.' }),
                jsx('div', {
                  className: 'text-[0.6875rem] leading-relaxed text-(--ui-text-tertiary)',
                  children: 'Try again in a few seconds.'
                }),
                jsx(Button, { size: 'sm', variant: 'outline', onClick: () => query.refetch(), children: 'Retry' })
              ]
            })
          : jsx('div', {
              className: 'min-h-0 flex-1 overflow-y-auto overscroll-contain',
              style: { overflowY: 'auto', overscrollBehavior: 'contain' },
              role: 'region',
              'aria-label': 'Usage breakdown by provider',
              children: (data?.providers || []).map(provider =>
                jsx(CompactProvider, { provider, selectedValue: selectedForBar?.value }, provider.provider)
              )
            }),
      data?.privacy
        ? jsx('footer', {
            className: 'shrink-0 border-t border-(--ui-stroke-secondary) px-3 py-2 text-[0.5625rem] leading-relaxed text-(--ui-text-quaternary)',
            children: data.privacy
          })
        : null
    ]
  })
}

function UsageStatusLabel() {
  const query = useUsage()
  const selection = useValue($statusSelection)
  const runtime = useActiveRuntime()
  const { selected, fellBack } = selectedStatus(query.data, selection, runtime)
  const runtimeLabel = runtime.provider || runtime.model || 'unidentified provider'
  const reset = selected?.exhausted ? formatDate(selected.reset_at) : null
  const exhaustedNote = selected?.exhausted ? ` · exhausted${reset ? ` until ${reset}` : ''}` : ''
  return jsx('span', {
    title: selected
      ? `${selection === AUTO_SELECTION ? 'Automatic' : 'Pinned provider'} · ${runtimeLabel} · ${selected.provider} · ${selected.window}${exhaustedNote}${fellBack ? ' · previous selection unavailable' : ''}`
      : `Automatic · ${runtimeLabel} · no linked quota`,
    'data-exhausted': selected?.exhausted ? 'true' : undefined,
    children: selected?.remaining != null
      ? `AI ${formatPercent(selected.remaining)}${selected.exhausted ? ' · exhausted' : ''}`
      : 'AI —'
  })
}

export default {
  id: ID,
  name: 'AI Usage',
  defaultEnabled: true,
  register(ctx) {
    pluginCtx = ctx
    // The pre-1.4 cache had no profile in its key, so it may hold another profile's quota: drop it.
    ctx.storage.remove(LEGACY_USAGE_CACHE_KEY)
    const scope = readUsageScope()
    const existingUsage = queryClient.getQueryData(scope.queryKey)
    if (existingUsage?.providers?.length && !existingUsage.transport_stale) {
      saveUsageCache(existingUsage, scope)
    }
    $statusSelection.set(ctx.storage.get(SELECTION_KEY, AUTO_SELECTION))

    host.onEvent('session.info', event => {
      const payload = event?.payload && typeof event.payload === 'object' ? event.payload : {}
      const sessionId = String(
        event?.session_id || payload.session_id || host.state.focusedSessionId.get() || host.state.activeSessionId.get() || ''
      ).trim()
      if (!sessionId) return

      const current = $runtimeInfoBySession.get()[sessionId] || {}
      const model = typeof payload.model === 'string' && payload.model.trim() ? payload.model.trim() : current.model || ''
      const provider =
        typeof payload.provider === 'string' && payload.provider.trim() ? payload.provider.trim() : current.provider || ''
      if (model === current.model && provider === current.provider) return

      $runtimeInfoBySession.set({
        ...$runtimeInfoBySession.get(),
        [sessionId]: { model, provider }
      })
    })

    ctx.register({
      id: 'usage-menu',
      area: STATUSBAR_AREAS.right,
      order: 125,
      data: {
        id: 'usage-dashboard-menu',
        label: jsx(UsageStatusLabel, {}),
        menuAlign: 'end',
        menuClassName: 'w-[27rem] max-w-[calc(100vw-1rem)] border-(--ui-stroke-secondary) p-0',
        menuContent: () => jsx(UsageCompactPanel, {}),
        title: 'View subscription usage',
        toggleLabel: 'AI Usage',
        variant: 'menu'
      }
    })
  }
}
