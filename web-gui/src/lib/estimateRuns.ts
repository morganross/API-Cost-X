// estimateRuns.ts — Pure function to estimate total LLM calls for a preset config

export interface RunEstimateInputs {
  // Documents
  documentCount: number

  // Generators (only count enabled ones)
  fpfEnabled: boolean
  fpfModelCount: number
  gptrEnabled: boolean
  gptrModelCount: number
  drEnabled: boolean
  drModelCount: number
  aiqEnabled: boolean
  aiqModelCount: number
  maEnabled: boolean
  maModelCount: number

  // Generation
  iterations: number

  // Evaluation
  evalEnabled: boolean
  pairwiseEnabled: boolean
  evalIterations: number
  judgeModelCount: number
  pairwiseTopN: number

  // Combine
  combineEnabled: boolean
  combineModelCount: number
}

export interface RunEstimateBreakdown {
  generation: number
  singleEval: number
  preCombinePairwise: number
  combine: number
  postCombinePairwise: number
  total: number
  // Per-doc breakdown for display
  perDoc: {
    generation: number
    singleEval: number
    preCombinePairwise: number
    combine: number
    postCombinePairwise: number
    total: number
  }
  documentCount: number
}

function choose2(n: number): number {
  if (n < 2) return 0
  return (n * (n - 1)) / 2
}

export function estimateRuns(inputs: RunEstimateInputs): RunEstimateBreakdown {
  const D = inputs.documentCount

  // Count total generation models across enabled generators
  let genModels = 0
  if (inputs.fpfEnabled) genModels += inputs.fpfModelCount
  if (inputs.gptrEnabled) genModels += inputs.gptrModelCount
  if (inputs.drEnabled) genModels += inputs.drModelCount
  if (inputs.aiqEnabled) genModels += inputs.aiqModelCount
  if (inputs.maEnabled) genModels += inputs.maModelCount

  const I = inputs.iterations
  const EI = inputs.evalIterations
  const J = inputs.judgeModelCount
  const Cm = inputs.combineModelCount

  // Phase 1: Generation
  const genPerDoc = genModels * I

  // Phase 2: Single Eval (only if eval enabled)
  const singleEvalPerDoc = inputs.evalEnabled ? genPerDoc * EI * J : 0

  // Phase 3: Pre-combine Pairwise (only if eval enabled and 2+ candidates)
  let prePwPerDoc = 0
  if (inputs.evalEnabled && inputs.pairwiseEnabled) {
    const n = Math.min(inputs.pairwiseTopN, genPerDoc)
    prePwPerDoc = choose2(n) * EI * J
  }

  // Phase 4: Combine (only if combine enabled)
  const combinePerDoc = inputs.combineEnabled ? Cm : 0

  // Phase 5: Post-combine Pairwise (only if combine enabled and eval enabled)
  let postPwPerDoc = 0
  if (inputs.combineEnabled && inputs.evalEnabled && inputs.pairwiseEnabled) {
    const m = 2 + Cm
    postPwPerDoc = choose2(m) * EI * J
  }

  const totalPerDoc = genPerDoc + singleEvalPerDoc + prePwPerDoc + combinePerDoc + postPwPerDoc

  return {
    generation: genPerDoc * D,
    singleEval: singleEvalPerDoc * D,
    preCombinePairwise: prePwPerDoc * D,
    combine: combinePerDoc * D,
    postCombinePairwise: postPwPerDoc * D,
    total: totalPerDoc * D,
    perDoc: {
      generation: genPerDoc,
      singleEval: singleEvalPerDoc,
      preCombinePairwise: prePwPerDoc,
      combine: combinePerDoc,
      postCombinePairwise: postPwPerDoc,
      total: totalPerDoc,
    },
    documentCount: D,
  }
}
