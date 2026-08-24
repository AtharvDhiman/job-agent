import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: '#0f172a', soft: '#1e293b', muted: '#475569' },
        surface: { DEFAULT: '#ffffff', sunken: '#f8fafc', raised: '#f1f5f9' },
        brand: { DEFAULT: '#1d4ed8', soft: '#dbeafe', dark: '#1e3a8a' },
        good: { DEFAULT: '#15803d', soft: '#dcfce7' },
        warn: { DEFAULT: '#b45309', soft: '#fef3c7' },
        bad: { DEFAULT: '#b91c1c', soft: '#fee2e2' },
      },
      fontFamily: {
        sans: ['ui-sans-serif', 'system-ui', 'Segoe UI', 'Roboto', 'Helvetica', 'Arial'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}

export default config
