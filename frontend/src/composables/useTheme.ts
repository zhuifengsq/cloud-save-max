import { computed, ref, watch } from 'vue'

export type ThemePreference = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'

const STORAGE_KEY = 'theme'

function readStoredPreference(): ThemePreference {
  if (typeof window === 'undefined') return 'system'
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    return stored === 'light' || stored === 'dark' ? stored : 'system'
  } catch {
    return 'system'
  }
}

function readSystemDark(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

const preference = ref<ThemePreference>(readStoredPreference())
const systemDark = ref<boolean>(readSystemDark())
let listenersBound = false

function bindGlobalListeners() {
  if (listenersBound || typeof window === 'undefined') return
  listenersBound = true

  if (window.matchMedia) {
    const mql = window.matchMedia('(prefers-color-scheme: dark)')
    const onSystemChange = (event: MediaQueryListEvent) => {
      systemDark.value = event.matches
    }
    if (mql.addEventListener) mql.addEventListener('change', onSystemChange)
    else (mql as any).addListener(onSystemChange)
  }

  window.addEventListener('storage', (event: StorageEvent) => {
    if (event.key !== STORAGE_KEY && event.key !== null) return
    preference.value = readStoredPreference()
  })
}

const resolvedTheme = computed<ResolvedTheme>(() => {
  if (preference.value === 'light' || preference.value === 'dark') {
    return preference.value
  }
  return systemDark.value ? 'dark' : 'light'
})

function syncDocumentTheme(pref: ThemePreference, resolved: ResolvedTheme) {
  if (typeof document !== 'undefined') {
    const meta = document.querySelector('meta[name="color-scheme"]')
    if (meta) {
      meta.setAttribute('content', pref === 'system' ? 'light dark' : pref)
    }
    document.documentElement.dataset.theme = resolved
  }
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('theme-change', { detail: resolved }))
  }
}

watch(
  [preference, resolvedTheme],
  ([pref, resolved]) => {
    syncDocumentTheme(pref, resolved)
  },
  { immediate: true },
)

const isDark = computed<boolean>({
  get: () => resolvedTheme.value === 'dark',
  set: (val: boolean) => {
    const target: ResolvedTheme = val ? 'dark' : 'light'
    const currentSystem: ResolvedTheme = systemDark.value ? 'dark' : 'light'
    if (target === currentSystem) {
      preference.value = 'system'
      try {
        window.localStorage.removeItem(STORAGE_KEY)
      } catch {}
      return
    }
    preference.value = target
    try {
      window.localStorage.setItem(STORAGE_KEY, target)
    } catch {}
  },
})

export function useTheme() {
  bindGlobalListeners()
  return {
    preference,
    resolvedTheme,
    isDark,
  }
}
