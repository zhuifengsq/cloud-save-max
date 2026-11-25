export function runViewTransition(update: () => void | Promise<void>, focusSelector?: string): void {
  const focusTarget = () => {
    if (!focusSelector || typeof document === 'undefined') return
    document.querySelector<HTMLElement>(focusSelector)?.focus()
  }

  const reducedMotion =
    typeof window !== 'undefined' &&
    Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)

  const startTransition =
    typeof document !== 'undefined' && typeof (document as any).startViewTransition === 'function'
      ? (document as any).startViewTransition.bind(document)
      : null

  if (!startTransition || reducedMotion) {
    Promise.resolve(update()).finally(focusTarget)
    return
  }

  const transition = startTransition(() => update())
  transition?.finished?.finally(focusTarget)
}
