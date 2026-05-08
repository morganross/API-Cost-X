export function isDesktopShell(): boolean {
  if (typeof window === 'undefined') {
    return false
  }

  return new URLSearchParams(window.location.search).get('shell') === 'desktop'
}

export function applyDesktopShellClass(): void {
  if (typeof document === 'undefined') {
    return
  }

  document.documentElement.classList.toggle('apicostx-desktop-shell', isDesktopShell())
}
