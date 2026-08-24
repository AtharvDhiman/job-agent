import type { Metadata } from 'next'

import './globals.css'

export const metadata: Metadata = {
  title: 'Job Application Agent',
  description:
    'Finds newly posted jobs, matches them to your verified career facts, and drafts truthful applications. Review-first by default.',
  robots: { index: false, follow: false },
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  )
}
