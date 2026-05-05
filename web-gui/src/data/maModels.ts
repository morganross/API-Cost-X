// SSOT for Multi-Agent model options shown in MultiAgentPanel.
// Add or remove entries here; the component reads this array directly.

export interface MaModelOption {
  value: string
  label: string
}

export const MA_MODELS: MaModelOption[] = [
  { value: 'gpt-4o',              label: 'GPT-4o' },
  { value: 'gpt-4o-mini',         label: 'GPT-4o Mini' },
  { value: 'claude-3-5-sonnet',   label: 'Claude 3.5 Sonnet' },
  { value: 'claude-3-opus',       label: 'Claude 3 Opus' },
  { value: 'gemini-1.5-pro',      label: 'Gemini 1.5 Pro' },
  { value: 'gemini-2.0-flash',    label: 'Gemini 2.0 Flash' },
]
