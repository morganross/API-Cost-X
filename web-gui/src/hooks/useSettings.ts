import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'

// Concurrency settings interface - shared with Settings.tsx
export interface ConcurrencySettings {
  generationConcurrency: number
  evalConcurrency: number
  requestTimeout: number | null
  evalTimeout: number | null
  maxRetries: number
  retryDelay: number
  iterations: number
  evalIterations: number
  fpfLogOutput: 'stream' | 'file' | 'none'
  fpfLogFilePath: string
  postCombineTopN: number | null
}

interface Settings {
  apiKeys: {
    openai?: string
    anthropic?: string
    google?: string
  }
  defaults: {
    generatorAdapter: 'fpf' | 'gptr'
    generatorModel: string
    evaluatorModel: string
    rubricType: 'scale_1_5' | 'binary' | 'percentage'
  }
  concurrency: ConcurrencySettings
  advanced: {
    databasePath: string
    reportsDirectory: string
  }
}

const defaultConcurrency: ConcurrencySettings = {
  generationConcurrency: 5,
  evalConcurrency: 5,
  requestTimeout: null,
  evalTimeout: null,
  maxRetries: 3,
  retryDelay: 2.0,
  iterations: 1,
  evalIterations: 1,
  fpfLogOutput: 'file',
  fpfLogFilePath: 'logs/{run_id}/fpf_output.log',
  postCombineTopN: 5,
}

const defaultSettings: Settings = {
  apiKeys: {},
  defaults: {
    generatorAdapter: 'fpf',
    generatorModel: 'gpt-5',
    evaluatorModel: 'gpt-5',
    rubricType: 'scale_1_5',
  },
  concurrency: defaultConcurrency,
  advanced: {
    databasePath: '~/.apicostx/apicostx.db',
    reportsDirectory: '~/.apicostx/reports',
  },
}

// In a real app, these would call the API
async function fetchSettings(): Promise<Settings> {
  // return apiClient.get<Settings>('/settings')
  return defaultSettings
}

async function updateSettings(settings: Partial<Settings>): Promise<Settings> {
  // return apiClient.put<Settings>('/settings', settings)
  return { ...defaultSettings, ...settings }
}

export function useSettings() {
  return useQuery({
    queryKey: ['settings'],
    queryFn: fetchSettings,
    staleTime: Infinity, // Settings don't change often
  })
}

export function useUpdateSettings() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: updateSettings,
    onSuccess: (data) => {
      queryClient.setQueryData(['settings'], data)
    },
  })
}
