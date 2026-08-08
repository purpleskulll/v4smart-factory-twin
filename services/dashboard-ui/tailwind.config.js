/** Design-Tokens aus CLAUDE.md §16 (dunkles Industrie-Theme). */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0B0F14',
        surface: '#111820',
        edge: '#1C2733',
        text: '#E5E7EB',
        muted: '#94A3B8',
        ok: '#22C55E',
        throttled: '#F59E0B',
        error: '#EF4444',
        offline: '#6B7280',
        accent: '#38BDF8',
      },
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
