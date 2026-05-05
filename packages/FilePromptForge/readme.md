# File Prompt Forge (FPF)

FPF rules (hard-enforced)
- Always uses provider-side web search (grounding) for every request.
- Always requests and requires model reasoning (thinking/rationale).
- These behaviors are mandatory and non-configurable. Any response lacking grounding or reasoning causes the run to fail, and no human-readable report is written.

## Intelligent Retry System (4-Layer Architecture)

**Exit Code Protocol:**
- `0` = Success (validation passed)
- `1` = Validation failure: missing grounding only
- `2` = Validation failure: missing reasoning only
- `3` = Validation failure: missing both grounding and reasoning
- `4` = Validation failure: unknown type
- `5` = Other errors (network, API, etc.)

**Retry Behavior:**
- Validation failures (exit codes 1-4) automatically trigger up to 2 retries (3 total attempts)
- Exponential backoff: 1s (attempt 1), 2s (attempt 2), 4s (attempt 3)
- Each retry receives validation-specific enhanced prompts with escalating urgency
- Fallback detection scans for FAILURE-REPORT.json files if exit code is 0
- Comprehensive logging of failure type, attempt number, backoff duration, and outcome

**Enhanced Prompts on Retry:**
- Grounding failures: Emphasizes web search requirements, citation format, verification steps
- Reasoning failures: Emphasizes chain-of-thought, step-by-step analysis, explicit reasoning
- Combined failures: Applies both enhancement strategies with highest urgency level

Supported providers
- Provider adapters handle provider-specific payloads and verification:
  - OpenAI (Responses API) — allowed models: gpt-5, gpt-5-mini, gpt-5-nano, gpt-5.1, gpt-5.1-mini, gpt-5.1-preview, o4-mini, o3 (prefix-tolerant)
  - OpenAI Deep Research (openaidp family) — allowed models: o3-deep-research, o4-mini-deep-research (prefix-tolerant)
  - Google Gemini — allowed models: gemini-1.5-pro, gemini-1.5-flash, gemini-2.0-flash, gemini-2.5-flash, gemini-2.5-flash-lite, gemini-2.5-pro, gemini-3-pro-preview, gemini-pro-latest (prefix-tolerant)
  - Tavily Research — allowed models: tavily/tvly-mini, tavily/tvly-pro, tavily/auto (normalized to mini/pro/auto)  - OpenRouter — gateway to 600+ models; grounding NOT enforced (REQUIRES_GROUNDING=False); model format: provider/model (e.g., openai/gpt-4o, anthropic/claude-sonnet-4)- fpf_main acts as a router so caller code doesn’t need per-model syntax knowledge.

Key implementation details
- Provider adapters always attach:
  - A web search tool (e.g., web_search_preview for OpenAI; google_search for Gemini)
  - A reasoning configuration appropriate to the model family
  - Exception: OpenRouter does not enforce web search (most models don't support it)
- Adapters expose execute_and_verify(...) and verify that the model actually used grounding and produced reasoning before returning.
- A shared grounding_enforcer module asserts both signals (grounding + reasoning) across providers.
- Providers can set REQUIRES_GROUNDING=False or REQUIRES_REASONING=False to skip enforcement.
- file_handler calls provider.execute_and_verify when available; otherwise it posts and then runs grounding_enforcer.assert_grounding_and_reasoning. If verification fails, FPF errors and refuses to write a report.

Configuration notes
- fpf_config.yaml is still read for general settings (model, max tokens, headers, template). However:
  - Any settings that would disable grounding or reasoning are ignored for enforcement.
  - Grounding and reasoning are not user-configurable; they are always on and strictly required.
- provider_urls maps providers to their HTTP endpoints (OpenAI/Deep Research/Gemini).

Files
- fpf_main.py: CLI entry and routing.
- file_handler.py: core orchestration and mandatory enforcement at write-time.
- providers/*: provider adapters (OpenAI, OpenAI Deep Research, Google Gemini) with execute_and_verify hooks.
- grounding_enforcer.py: centralized verification for grounding and reasoning.
- fpf_config.yaml: default configuration (does not control grounding/reasoning enforcement).
- test/: inputs and future tests.

Behavior
- Loads values from fpf_config.yaml.
- Takes two input files, composes a prompt, routes to the configured provider adapter, and enforces grounding + reasoning.
- On success, the human-readable output is saved as a file next to the second input file (path may be configured).

CLI usage
- Ensure an appropriate API key is present in the process environment or repository root `.env` file.
- Example:
  - python fpf_main.py --file-a path/to/a.txt --file-b path/to/b.md
  - python fpf_main.py --file-a a.txt --file-b b.md --model gpt-5-mini
  - python fpf_main.py --file-a a.txt --file-b b.md --out result.txt

Failure modes (by design)
- If the provider did not perform grounding (web search/citations) or did not return reasoning, FPF fails the run and will not write the report output.
- If the selected model cannot support enforced reasoning parameters, the adapter will fail fast via validate_model.

References
- OpenAI Tools — Web Search (Responses API):
  - https://platform.openai.com/docs/guides/tools-web-search?api-mode=responses
