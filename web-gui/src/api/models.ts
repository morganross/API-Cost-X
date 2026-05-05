import { apiClient } from './client'

export interface ModelInfo {
  sections: string[];
  max_output_tokens: number | null;
  dr_native?: boolean;  // Deep Research native models (autonomous research)
}

export interface ModelConfigResponse {
  models: Record<string, ModelInfo>;
}

export const modelsApi = {
  getModels: async (): Promise<ModelConfigResponse> => {
    return await apiClient.get<ModelConfigResponse>('/models');
  },
};
