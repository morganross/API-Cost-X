export type ProviderId = 'openai' | 'anthropic' | 'google' | 'openrouter' | 'perplexity' | 'tavily' | 'github'

export interface ProviderDescriptor {
  id: ProviderId
  label: string
  envVar: string
  description: string
}

export interface ProviderKeyRecord {
  provider: string
  configured: boolean
  maskedKey: string | null
}

export const PROVIDER_OPTIONS: ProviderDescriptor[] = [
  { id: 'openai', label: 'OpenAI', envVar: 'OPENAI_API_KEY', description: 'Used for GPT models.' },
  { id: 'anthropic', label: 'Anthropic', envVar: 'ANTHROPIC_API_KEY', description: 'Used for Claude models.' },
  { id: 'google', label: 'Google', envVar: 'GOOGLE_API_KEY', description: 'Used for Gemini models.' },
  { id: 'openrouter', label: 'OpenRouter', envVar: 'OPENROUTER_API_KEY', description: 'Used for OpenRouter-routed models.' },
  { id: 'perplexity', label: 'Perplexity', envVar: 'PERPLEXITY_API_KEY', description: 'Used for native Perplexity models.' },
  { id: 'tavily', label: 'Tavily', envVar: 'TAVILY_API_KEY', description: 'Used for Tavily search and research tools.' },
  { id: 'github', label: 'GitHub', envVar: 'GITHUB_TOKEN', description: 'Used for GitHub-backed import/export.' },
]

export const providerKeysApi = {
  async list(): Promise<ProviderKeyRecord[]> {
    // Self-hosted secrets live only in root .env. The browser cannot inspect them.
    return []
  },

  async save(): Promise<void> {
    throw new Error('Provider keys are configured in the root .env file, not through the GUI.')
  },

  async remove(): Promise<void> {
    throw new Error('Provider keys are configured in the root .env file, not through the GUI.')
  },
}
