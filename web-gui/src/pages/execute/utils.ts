// Score badge styling for 1-5 scale scores
// Matches html_exporter.py: score-perfect/great/mid/low; ensure readable text contrast.
export function getScoreBadgeStyle(score: number | undefined): { bg: string; text: string; label: string } {
  if (score === undefined) return { bg: '#e9ecef', text: '#333', label: '—' }
  // Handle 1-5 scale (evaluation scores)
  const raw = score > 1 ? score : score * 5
  if (raw >= 4.5) return { bg: '#28a745', text: 'white', label: 'Excellent' } // score-perfect
  if (raw >= 3.5) return { bg: '#90EE90', text: '#006400', label: 'Great' }  // score-great
  if (raw >= 2.5) return { bg: '#ffc107', text: '#333', label: 'Fair' }       // score-mid
  if (raw >= 1.5) return { bg: '#fd7e14', text: '#1a1a1a', label: 'Low' }
  return { bg: '#dc3545', text: 'white', label: 'Poor' }                      // score-low
}

// Heatmap-style color for pairwise matrix cells (red→amber→green)
export function getPairwiseHeatColor(winRate: number): { bg: string; text: string } {
  // Use dark text except on the two deepest reds/greens to avoid low contrast.
  if (winRate >= 80) return { bg: '#0f3d26', text: 'white' } // deep green
  if (winRate >= 60) return { bg: '#166534', text: 'white' }
  if (winRate >= 50) return { bg: '#f0b429', text: '#1a1200' } // amber with dark text
  if (winRate >= 25) return { bg: '#f08c42', text: '#1a1200' } // lighter amber, dark text
  if (winRate > 0) return { bg: '#b71c1c', text: 'white' }
  return { bg: '#e9ecef', text: '#212529' }
}

// Helper to format ISO timestamp as HH:MM:SS
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return '—'
  }
}

// Helper to compute end time: use completed_at if available, else start + duration
export function computeEndTime(
  startIso: string | null | undefined,
  completedAt: string | null | undefined,
  durationSeconds: number | null | undefined
): string {
  if (completedAt) return formatTime(completedAt)
  if (startIso) {
    // If running (no completedAt), show elapsed time if duration is available, or "Running..."
    if (durationSeconds != null) {
      // Format duration as HH:MM:SS
      const h = Math.floor(durationSeconds / 3600);
      const m = Math.floor((durationSeconds % 3600) / 60);
      const s = Math.floor(durationSeconds % 60);
      return `Running: ${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
    }
    // Fallback if no duration yet
    return 'Running...'
  }
  return '—'
}
