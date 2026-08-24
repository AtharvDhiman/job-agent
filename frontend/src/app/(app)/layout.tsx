import { redirect } from 'next/navigation'

import { Shell } from '@/components/Shell'
import { readAccessToken, readRefreshToken } from '@/lib/session'

export default function AppLayout({ children }: { children: React.ReactNode }) {
  if (!readAccessToken() && !readRefreshToken()) {
    redirect('/login')
  }
  return <Shell>{children}</Shell>
}
