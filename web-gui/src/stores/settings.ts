import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface UISettings {
  sidebarCollapsed: boolean
  theme: 'light' | 'dark' | 'system'
  defaultExpandedSections: {
    documents: boolean
    generator: boolean
    evaluator: boolean
  }
}

interface SettingsStore extends UISettings {
  setSidebarCollapsed: (collapsed: boolean) => void
  setTheme: (theme: 'light' | 'dark' | 'system') => void
  setDefaultExpandedSections: (sections: Partial<UISettings['defaultExpandedSections']>) => void
}

export const useSettingsStore = create<SettingsStore>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      theme: 'system',
      defaultExpandedSections: {
        documents: true,
        generator: true,
        evaluator: true,
      },
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
      setTheme: (theme) => set({ theme }),
      setDefaultExpandedSections: (sections) =>
        set((state) => ({
          defaultExpandedSections: {
            ...state.defaultExpandedSections,
            ...sections,
          },
        })),
    }),
    {
      name: 'apicostx-settings',
    }
  )
)
