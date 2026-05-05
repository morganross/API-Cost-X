import GitHubConnectionsPanel from '@/components/settings/GitHubConnectionsPanel'

const ENV_KEYS = [
  'OPENAI_API_KEY',
  'ANTHROPIC_API_KEY',
  'GOOGLE_API_KEY',
  'OPENROUTER_API_KEY',
  'GROQ_API_KEY',
  'PERPLEXITY_API_KEY',
  'TAVILY_API_KEY',
  'GITHUB_TOKEN',
]

export default function Settings() {
  return (
    <div className="min-h-screen bg-gray-900 text-gray-100">
      <div className="max-w-5xl mx-auto px-4 py-6 space-y-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-100">Settings</h1>
          <p className="mt-2 text-sm text-gray-400">
            Self-hosted mode keeps every secret in one file: the root <code className="font-mono text-gray-200">.env</code> next to <code className="font-mono text-gray-200">start.sh</code>.
          </p>
        </div>

        <div className="bg-gray-800 border border-gray-700 rounded-lg p-5 space-y-4">
          <div>
            <h2 className="font-semibold text-gray-100">Root .env Secrets</h2>
            <p className="mt-1 text-sm text-gray-400">
              Secrets are not saved through the GUI and are not stored in SQLite. Edit the root <code className="font-mono text-gray-200">.env</code> file, then restart the app.
            </p>
          </div>

          <div className="rounded-md border border-gray-700 bg-gray-900 p-4">
            <p className="text-xs uppercase tracking-wide text-gray-500 mb-3">Supported keys</p>
            <div className="grid gap-2 sm:grid-cols-2">
              {ENV_KEYS.map((key) => (
                <code key={key} className="rounded bg-gray-800 px-2 py-1 text-sm text-gray-200">
                  {key}=
                </code>
              ))}
            </div>
          </div>

          <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-100">
            Keep <code className="font-mono">.env</code> private. The public repo should include only <code className="font-mono">.env.example</code> with blank/commented examples.
          </div>
        </div>

        <GitHubConnectionsPanel />
      </div>
    </div>
  )
}
