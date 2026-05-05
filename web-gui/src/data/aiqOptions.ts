export interface AiqOption {
  value: string
  label: string
}

export const AIQ_PROFILE_OPTIONS: AiqOption[] = [
  {
    value: 'deep_web_default',
    label: 'deep_web_default - Web + LlamaIndex',
  },
  {
    value: 'frontier_models',
    label: 'frontier_models - Web + Frontier Hybrid',
  },
  {
    value: 'web_frag',
    label: 'web_frag - Web + Foundational RAG',
  },
]

export const AIQ_AGENT_TYPE_OPTIONS: AiqOption[] = [
  {
    value: 'deep_researcher',
    label: 'deep_researcher',
  },
]

export const AIQ_DATA_SOURCE_OPTIONS: AiqOption[] = [
  {
    value: 'web',
    label: 'web',
  },
]

export const AIQ_KNOWLEDGE_BACKEND_OPTIONS: AiqOption[] = [
  {
    value: 'llamaindex',
    label: 'llamaindex',
  },
  {
    value: 'foundational_rag',
    label: 'foundational_rag',
  },
]

const SHARED_PROFILE_LLM_OPTIONS: AiqOption[] = [
  {
    value: 'nemotron_llm_intent',
    label: 'nemotron_llm_intent - nvidia/nemotron-3-nano-30b-a3b',
  },
  {
    value: 'nemotron_nano_llm',
    label: 'nemotron_nano_llm - nvidia/nemotron-3-nano-30b-a3b',
  },
  {
    value: 'gpt_oss_llm',
    label: 'gpt_oss_llm - openai/gpt-oss-120b',
  },
  {
    value: 'summary_llm',
    label: 'summary_llm - nvidia/nemotron-mini-4b-instruct',
  },
]

export const AIQ_LLM_BINDING_OPTIONS_BY_PROFILE: Record<string, AiqOption[]> = {
  deep_web_default: SHARED_PROFILE_LLM_OPTIONS,
  web_frag: SHARED_PROFILE_LLM_OPTIONS,
  frontier_models: [
    {
      value: 'nemotron_llm_intent',
      label: 'nemotron_llm_intent - nvidia/nemotron-3-nano-30b-a3b',
    },
    {
      value: 'nemotron_nano_llm',
      label: 'nemotron_nano_llm - nvidia/nemotron-3-nano-30b-a3b',
    },
    {
      value: 'openai_gpt_5_2',
      label: 'openai_gpt_5_2 - gpt-5.2',
    },
    {
      value: 'summary_llm',
      label: 'summary_llm - nvidia/nemotron-mini-4b-instruct',
    },
  ],
}

export const AIQ_MODEL_NAME_OPTIONS: AiqOption[] = [
  {
    value: 'nvidia/nemotron-3-nano-30b-a3b',
    label: 'nvidia/nemotron-3-nano-30b-a3b',
  },
  {
    value: 'openai/gpt-oss-120b',
    label: 'openai/gpt-oss-120b',
  },
  {
    value: 'nvidia/nemotron-mini-4b-instruct',
    label: 'nvidia/nemotron-mini-4b-instruct',
  },
  {
    value: 'gpt-5.2',
    label: 'gpt-5.2',
  },
  {
    value: 'nvidia/nemotron-3-super-120b-a12b',
    label: 'nvidia/nemotron-3-super-120b-a12b',
  },
]
