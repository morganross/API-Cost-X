import { create } from 'zustand';
import { modelsApi, ModelInfo } from '../api/models';
import { getJudgeQuality } from '../data/judgeQualityScores';
import { getAiqScore, getGenScore } from '../data/genModelScores';

type CatalogSortBy =
  | 'name'
  | 'judgeQuality'
  | 'genQualityFpf'
  | 'genQualityGptr'
  | 'genQualityDr'
  | 'genQualityAiq'
  | 'genScore';

interface ModelCatalogState {
  models: Record<string, ModelInfo>;
  isLoading: boolean;
  error: string | null;
  sortBy: CatalogSortBy;
  sortDir: 'asc' | 'desc';

  fpfModels: string[];
  fpfFreeModels: string[];
  gptrModels: string[];
  gptrFreeModels: string[];
  drModels: string[];
  drFreeModels: string[];
  evalModels: string[];
  evalFreeModels: string[];
  combineModels: string[];
  combineFreeModels: string[];

  fetchModels: () => Promise<void>;
  getMaxOutputTokens: (modelKey: string) => number | null;
  isDrNative: (modelKey: string) => boolean;
  setSortBy: (method: CatalogSortBy) => void;
  getSortedModels: (models: string[], genType?: 'fpf' | 'gptr' | 'dr') => string[];
}

function sortByOptionalNumber(
  models: string[],
  sortDir: 'asc' | 'desc',
  getMetric: (model: string) => number | null | undefined
) {
  models.sort((a, b) => {
    const qa = getMetric(a) ?? Number.NEGATIVE_INFINITY;
    const qb = getMetric(b) ?? Number.NEGATIVE_INFINITY;

    const bothMissing = !Number.isFinite(qa) && !Number.isFinite(qb);
    if (bothMissing) return a.localeCompare(b);
    if (!Number.isFinite(qa)) return 1;
    if (!Number.isFinite(qb)) return -1;

    const diff = qa - qb;
    if (diff !== 0) return sortDir === 'asc' ? diff : -diff;
    return a.localeCompare(b);
  });
}

export const useModelCatalog = create<ModelCatalogState>((set, get) => ({
  models: {},
  isLoading: false,
  error: null,
  sortBy: 'judgeQuality',
  sortDir: 'desc',
  fpfModels: [],
  fpfFreeModels: [],
  gptrModels: [],
  gptrFreeModels: [],
  drModels: [],
  drFreeModels: [],
  evalModels: [],
  evalFreeModels: [],
  combineModels: [],
  combineFreeModels: [],

  fetchModels: async () => {
    set({ isLoading: true, error: null });
    try {
      const modelsResponse = await modelsApi.getModels();
      const models = modelsResponse.models;

      const hasSection = (modelKey: string, section: string) => models[modelKey].sections.includes(section);
      const isFree = (modelKey: string) => hasSection(modelKey, 'free');
      const fpfModels = Object.keys(models).filter(m => hasSection(m, 'fpf') && !isFree(m));
      const fpfFreeModels = Object.keys(models).filter(m => hasSection(m, 'fpf') && isFree(m));
      const gptrModels = Object.keys(models).filter(m => hasSection(m, 'gpt-r') && !isFree(m));
      const gptrFreeModels = Object.keys(models).filter(m => hasSection(m, 'gpt-r') && isFree(m));
      const drModels = Object.keys(models).filter(m => hasSection(m, 'dr') && !isFree(m));
      const drFreeModels = Object.keys(models).filter(m => hasSection(m, 'dr') && isFree(m));
      const evalModels = Object.keys(models).filter(m => hasSection(m, 'eval') && !isFree(m) && !models[m].dr_native);
      const evalFreeModels = Object.keys(models).filter(m => hasSection(m, 'eval') && isFree(m) && !models[m].dr_native);
      const combineModels = Object.keys(models).filter(m => hasSection(m, 'fpf') && !isFree(m) && !models[m].dr_native);
      const combineFreeModels = Object.keys(models).filter(m => hasSection(m, 'fpf') && isFree(m) && !models[m].dr_native);

      set({
        models,
        fpfModels,
        fpfFreeModels,
        gptrModels,
        gptrFreeModels,
        drModels,
        drFreeModels,
        evalModels,
        evalFreeModels,
        combineModels,
        combineFreeModels,
        isLoading: false,
      });
    } catch (error) {
      console.error('Failed to fetch models:', error);
      set({ error: 'Failed to load model list', isLoading: false });
    }
  },

  getMaxOutputTokens: (modelKey: string): number | null => {
    const model = get().models[modelKey];
    return model?.max_output_tokens ?? null;
  },

  isDrNative: (modelKey: string): boolean => {
    const model = get().models[modelKey];
    return model?.dr_native === true;
  },

  setSortBy: (method: CatalogSortBy) => {
    const { sortBy, sortDir } = get();
    if (method === sortBy) {
      set({ sortDir: sortDir === 'asc' ? 'desc' : 'asc' });
      return;
    }

    set({ sortBy: method, sortDir: method === 'name' ? 'asc' : 'desc' });
  },

  getSortedModels: (models: string[], genType?: 'fpf' | 'gptr' | 'dr'): string[] => {
    const { sortBy, sortDir } = get();
    const hasAnyScoreData = (model: string) =>
      !!(getJudgeQuality(model) || getGenScore('fpf', model) || getGenScore('gptr', model) || getGenScore('dr', model) || getAiqScore(model));
    const isNameSort = sortBy === 'name';
    const noScore = isNameSort ? [] : models.filter(model => !hasAnyScoreData(model)).sort((a, b) => a.localeCompare(b));
    const sorted = isNameSort ? [...models] : models.filter(hasAnyScoreData);

    if (isNameSort) {
      sorted.sort((a, b) => sortDir === 'asc' ? a.localeCompare(b) : b.localeCompare(a));
    } else if (sortBy === 'judgeQuality') {
      sortByOptionalNumber(sorted, sortDir, model => getJudgeQuality(model)?.sortino);
    } else if (sortBy === 'genQualityFpf' || sortBy === 'genQualityGptr' || sortBy === 'genQualityDr' || sortBy === 'genQualityAiq') {
      const type = sortBy === 'genQualityFpf' ? 'fpf' : sortBy === 'genQualityGptr' ? 'gptr' : sortBy === 'genQualityDr' ? 'dr' : 'aiq';
      sortByOptionalNumber(sorted, sortDir, model => getGenScore(type, model)?.score);
    } else if (sortBy === 'genScore') {
      sortByOptionalNumber(sorted, sortDir, model =>
        genType ? getGenScore(genType, model)?.score : getJudgeQuality(model)?.sortino
      );
    }

    return [...sorted, ...noScore];
  },
}));
