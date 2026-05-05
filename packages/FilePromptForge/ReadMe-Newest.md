# File Prompt Forge (FPF) Project Overview

The File Prompt Forge (FPF) project is a specialized tool designed to compose LLM prompts from two input files, dispatching a single non-streaming API request to a configured provider (currently OpenAI). A core principle of FPF is the strict enforcement of provider-side web-search grounding and mandatory model reasoning for all production runs. This ensures that generated outputs are well-supported by external information and provide transparent reasoning.

## Key Components

*   **`fpf_main.py`**: The main command-line interface (CLI) entry point for FPF. It handles argument parsing, logging setup, path resolution, and orchestrates the prompt composition and API call process by delegating to `file_handler.py`. It supports both single runs and batch processing via `fpf_config.yaml` or standard input.
*   **`file_handler.py`**: This is the central orchestration logic for FPF. It constructs the payload for the LLM API call, interacts with the LLM provider, manages sidecar files (raw responses, reasoning), and enforces the critical web-search and reasoning validation checks.
*   **`fpf_config.yaml`**: The primary configuration file for FPF. It defines:
    *   `concurrency`: Settings for concurrent batch processing requests, including `enabled`, `max_concurrency`, `qps` (queries per second), `retry` parameters, and `timeout_seconds`.
    *   `include`: Fields to request in the API response (e.g., `web_search_call.action.sources`).
    *   `json`: Boolean indicating if a JSON response is expected.
    *   `max_completion_tokens`: Maximum tokens for the LLM's completion.
    *   `model`: The default LLM model ID to use (e.g., `gpt-5`).
    *   `prompt_template`: An optional path or inline template for composing the prompt using `{{file_a}}` and `{{file_b}}`.
    *   `provider`: The default LLM provider (e.g., `openai`).
    *   `provider_urls`: Mappings of provider names to their API endpoints.
    *   `test`: Default paths for `file_a`, `file_b`, and `out` files used during testing or when not specified via CLI.
    *   `web_search`: Parameters for web search tuning, including `search_context_size` and a `search_prompt`.
*   **`providers/` directory**: Contains submodules for integrating with different LLM providers. Currently, `openai/fpf_openai_main.py` handles the OpenAI Responses API, including payload construction, response parsing, reasoning extraction, and model whitelisting.
*   **Secrets**: Provider API keys are read from the process environment first, then from the repository root `.env` file. Package-local `.env` files are not supported as a secret source.
*   **`scheduler.py`**: (Used by `fpf_main.py` for batch processing) Manages the scheduling and execution of multiple FPF runs, respecting concurrency limits defined in `fpf_config.yaml`.

## Core Functionality Highlights

*   **Prompt Composition**: Dynamically creates LLM prompts from the content of two separate input files (`file_a` and `file_b`), optionally using a custom `prompt_template`.
*   **Web-Search Grounding**: Enforces that the LLM performs provider-side web searches (for allowed models) to ground its responses, ensuring currency and factual accuracy.
*   **Explicit Reasoning**: Mandates that the LLM provides explicit reasoning for its output. Runs failing to demonstrate reasoning or web-search invocation will not produce a final human-readable report.
*   **Configurable Providers/Models**: Although currently focused on OpenAI, the architecture supports different providers and allows model selection via `fpf_config.yaml` and CLI overrides.

## Getting Started and Usage

### Quick Start (Local Execution)

1.  **Prerequisites**: Python 3.11+ and an active internet connection.
2.  **API Key Setup**: Export provider API keys in the process environment, or place them in the repository root `.env` file. For example:
    ```
    OPENAI_API_KEY=<your-openai-api-key>
    ```
    FPF does not read package-local `.env` files.
3.  **Configuration**: Review and, if necessary, modify `fpf_config.yaml` to suit your needs (e.g., set default input/output files, models).
4.  **Run a Single FPF Execution**:
    From the repository root, navigate to the vendored FilePromptForge directory:
    ```bash
    cd packages/FilePromptForge
    python fpf_main.py --verbose
    ```
    (This will use default test files and output paths defined in `fpf_config.yaml` or internal defaults.)

### Command-Line Arguments (`fpf_main.py`)

The `fpf_main.py` script supports various arguments for flexible execution:

*   `--file-a <path>`: Path to the first input file (left side of the prompt).
*   `--file-b <path>`: Path to the second input file (right side of the prompt).
*   `--out <path>`: Output path for the human-readable response.
*   `--config <path>`: Path to a custom `fpf_config.yaml` file (defaults to `packages/FilePromptForge/fpf_config.yaml` from the repository root).
*   `--env <path>`: Explicit path to the repository root `.env` file. Non-root env files are rejected for secrets.
*   `--provider <name>`: Override the default provider specified in the config.
*   `--model <id>`: Override the default model ID specified in the config.
*   `--reasoning-effort <level>`: Override the reasoning effort (e.g., `low`, `medium`, `high`).
*   `--max-completion-tokens <int>`: Override the maximum completion tokens for the LLM.
*   `--max-concurrency <int>`: Override the global maximum concurrency for batch mode.
*   `--runs-stdin`: Enables batch mode by reading a JSON array of run configurations from `stdin`.
*   `--batch-output {lines,json}`: Specifies the output format for batch mode (`lines` for one path per success, `json` for a JSON array of results). Default is `lines`.
*   `--verbose`, `-v`: Enable verbose logging for detailed execution insights.

**Example with specific files:**

```bash
cd packages/FilePromptForge
python fpf_main.py --file-a my_input1.txt --file-b my_prompt_template.txt --out ./results/my_output.md --model gpt-5-mini
```

## Audience-Specific Information

### For Developers

*   **Provider Adapters**: Extend `providers/` with new modules to add support for different LLM APIs. Each adapter should handle payload building, response parsing, and reasoning extraction specific to its provider.
*   **Enforcement Logic**: The core validation logic for web-search and reasoning resides in `file_handler.py`. Modifications to enforcement policies should be implemented there.
*   **Testing**: When developing, use the `--verbose` flag for detailed logging. The system produces `.raw.json` sidecar files containing the full API response, which are invaluable for debugging. Ensure changes are accompanied by local tests (running `fpf_main.py` and verifying generated artifacts).
*   **Custom Prompts**: The `prompt_template` mechanism offers flexibility for how `file_a` and `file_b` content is integrated into the final prompt.
*   **Logging**: All runs produce a rotating log file (`fpf_run.log`) in the `logs/` subdirectory for auditing and troubleshooting.

### For Users / Researchers

*   **Input Preparation**: Ensure your `file_a` (content) and `file_b` (instructions/template) are well-formed plain text or markdown files.
*   **Model Selection**: Consult the `openai_model_capabilities.md` report (if available) to understand which models support web-search grounding and reasoning, as FPF will fail otherwise.
*   **Interpreting Output**: On successful runs, FPF will output a human-readable markdown file, a `.{run_id}.raw.json` sidecar (raw API response), and a `.{run_id}.reasoning.txt` sidecar. Review the reasoning sidecar to understand the LLM's thought process.
*   **Batch Processing**: For multiple runs, leverage the `--runs-stdin` feature with a JSON array of configurations for efficient execution.

### For Operators / Administrators

*   **Concurrency Management**: Adjust `concurrency` settings in `fpf_config.yaml` (`max_concurrency`, `qps`, `timeout_seconds`) to optimize performance and prevent API rate limiting, especially during batch processing.
*   **API Key Security**: Provider API keys must come from the process environment or the repository root `.env` file. Do not hardcode or expose API keys elsewhere.
*   **Logging and Monitoring**: Regularly check `fpf_run.log` for operational status, errors, and performance insights.
*   **Troubleshooting**: Common issues like HTTP 400 errors (unknown parameters), timeouts, or missing reasoning/web-search are detailed in the `readme-difinitive.md` (which this README replaces). These usually point to model capabilities, API limits, or configuration errors.
