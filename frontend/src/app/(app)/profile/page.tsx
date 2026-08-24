'use client'

import { useState } from 'react'

import { Banner } from '@/components/ui'

import { AutopilotSection } from './AutopilotSection'
import { DocumentsSection } from './DocumentsSection'
import { FactsSection } from './FactsSection'
import { ProfileForm } from './ProfileForm'

export default function ProfilePage() {
  // Bumping this key remounts the facts table after an upload proposes new facts.
  const [factsKey, setFactsKey] = useState(0)

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold text-ink">Profile and career facts</h1>
        <p className="mt-1 text-sm text-ink-muted">
          Everything the agent is allowed to say about you lives here.
        </p>
      </header>

      <Banner tone="info">
        The agent will never invent a qualification, an employer, a date, a metric, a link, a
        visa status or a salary figure. It can only select and rephrase facts you have marked
        verified. If a form asks something it cannot answer from those facts, it stops and sends
        the job to your review queue instead of guessing.
      </Banner>

      {/* Keyed on factsKey so the readiness checklist refreshes when facts change. */}
      <AutopilotSection key={factsKey} />

      <ProfileForm />
      <FactsSection key={factsKey} />
      <DocumentsSection onFactsChanged={() => setFactsKey((n) => n + 1)} />
    </div>
  )
}
