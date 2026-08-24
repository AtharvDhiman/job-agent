import { redirect } from 'next/navigation'

import { readAccessToken } from '@/lib/session'

export default function Home() {
  redirect(readAccessToken() ? '/dashboard' : '/login')
}
