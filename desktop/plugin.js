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
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'usage-dashboard'
const QUERY_KEY = [ID, 'usage']
const USAGE_CACHE_KEY = 'last-good-usage'
const USAGE_CACHE_MAX_AGE_MS = 24 * 60 * 60 * 1000
const AUTO_SELECTION = '__auto_session__'
const SELECTION_KEY = 'statusbar-selection-v2'
const $statusSelection = atom(AUTO_SELECTION)
const $runtimeInfoBySession = atom({})
let pluginCtx = null

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
        remaining: window.remaining_percent == null ? null : clamp(window.remaining_percent)
      }))
  )
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

function priorityOption(options) {
  return options.reduce((preferred, option) => {
    if (!preferred || windowPriority(option.window) < windowPriority(preferred.window)) return option
    return preferred
  }, null)
}

function selectedStatus(data, selection, runtime) {
  const options = selectableWindows(data)
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
    detail: typeof window?.detail === 'string' ? window.detail : null
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
      ? (provider.windows || []).map((window) => window.remaining_percent).filter(Number.isFinite)
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

function saveUsageCache(payload) {
  if (payload?.providers?.some((provider) => provider.usage_fallback)) return
  pluginCtx.storage.set(USAGE_CACHE_KEY, {
    saved_at: Date.now(),
    payload: sanitizedUsage(payload)
  })
}

function loadUsageCache() {
  const entry = pluginCtx.storage.get(USAGE_CACHE_KEY, null)
  if (!entry?.payload || !Number.isFinite(entry.saved_at)) return null
  if (Date.now() - entry.saved_at > USAGE_CACHE_MAX_AGE_MS) {
    pluginCtx.storage.remove(USAGE_CACHE_KEY)
    return null
  }
  return sanitizedUsage(entry.payload)
}

async function fetchUsage(path = '/usage') {
  if (!pluginCtx) throw new Error('Usage plugin is not initialized')
  try {
    const payload = await pluginCtx.rest(path, { timeoutMs: 35000 })
    const clean = sanitizedUsage(payload)
    const previous = queryClient.getQueryData(QUERY_KEY) || loadUsageCache()
    const fresh = { ...mergeProviderFallback(clean, previous), transport_stale: false }
    saveUsageCache(fresh)
    return fresh
  } catch (error) {
    const memoryCache = queryClient.getQueryData(QUERY_KEY)
    const storedCache = loadUsageCache()
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
  return useQuery({
    queryKey: QUERY_KEY,
    queryFn: () => fetchUsage(),
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
          jsx('span', { className: 'min-w-0 truncate text-(--ui-text-secondary)', children: window.label }),
          window.remaining_percent == null
            ? jsx('span', { className: 'shrink-0 text-(--ui-text-quaternary)', children: 'No percentage' })
            : jsx('button', {
                className: cn(
                  'shrink-0 rounded px-1 py-0.5 font-semibold tabular-nums transition-colors hover:bg-(--chrome-action-hover)',
                  selected ? 'text-(--ui-accent)' : 'text-foreground'
                ),
                type: 'button',
                'aria-pressed': selected,
                title: 'Pin this provider to the status bar (session / 5 hours, then weekly, then monthly)',
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
      const payload = await fetchUsage('/usage')
      queryClient.setQueryData(QUERY_KEY, payload)
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
                : 'Click any percentage to pin its provider: session / 5 hours, then weekly, then monthly.'
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
  return jsx('span', {
    title: selected
      ? `${selection === AUTO_SELECTION ? 'Automatic' : 'Pinned provider'} · ${runtimeLabel} · ${selected.provider} · ${selected.window}${fellBack ? ' · previous selection unavailable' : ''}`
      : `Automatic · ${runtimeLabel} · no linked quota`,
    children: selected?.remaining != null ? `AI ${formatPercent(selected.remaining)}` : 'AI —'
  })
}

export default {
  id: ID,
  name: 'AI Usage',
  defaultEnabled: true,
  register(ctx) {
    pluginCtx = ctx
    const existingUsage = queryClient.getQueryData(QUERY_KEY)
    if (existingUsage?.providers?.length && !existingUsage.transport_stale) {
      saveUsageCache(existingUsage)
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
