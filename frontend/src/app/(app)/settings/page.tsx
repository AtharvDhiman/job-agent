'use client'

import { ErrorNote, useAsync } from '@/components/ui'
import { api } from '@/lib/api'
import type { Connector } from '@/lib/types'

import { AuthorizationsSection } from './AuthorizationsSection'
import { AutomationSection } from './AutomationSection'
import { PortalsSection } from './PortalsSection'
import { DataSection } from './DataSection'
import { SourcesSection } from './SourcesSection'

export default function SettingsPage() {
  const { data: connectors, error } = useAsync<Connector[]>(() =>
    api.get<Connector[]>('/connectors'),
  )

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold text-ink">Settings</h1>
        <p className="mt-1 text-sm text-ink-muted">
          Automation thresholds, the boards you search, what each platform is allowed to do, and
          your data.
        </p>
      </header>

      <AutomationSection />

      <ErrorNote message={error} />
      {connectors ? (
        <>
          <PortalsSection />
          <SourcesSection connectors={connectors} />
          <AuthorizationsSection connectors={connectors} />
        </>
      ) : (
        <p className="text-sm text-ink-muted">Loading connectors...</p>
      )}

      <DataSection />
    </div>
  )
}
