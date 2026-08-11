import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { notifications as notificationsApi } from '@/lib/api'
import { toast } from 'sonner'
import { BellRing, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { Notification } from '@/types'

const CRITICAL: Notification['alert_type'][] = ['3_DAYS', '1_DAY', 'DUE_DATE']

function isCritical(n: Notification) {
  return n.status === 'unread' && CRITICAL.includes(n.alert_type)
}

export function DueAlertsBanner() {
  const { t } = useTranslation()
  const [dismissed, setDismissed] = useState(false)

  const { data: items } = useQuery({
    queryKey: ['notifications'],
    queryFn: () => notificationsApi.list(),
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  })

  const critical = (items ?? []).filter(isCritical)
  if (dismissed || critical.length === 0) return null

  const dismiss = () => {
    setDismissed(true)
    Promise.all(critical.map((n) => notificationsApi.markRead(n.id))).catch(() => {
      toast.error(t('common.error'))
      setDismissed(false)
    })
  }

  const openNotifications = () => {
    window.dispatchEvent(new CustomEvent('talisma:open-notifications'))
  }

  return (
    <div className="sticky top-0 z-30 -mx-6 mb-6 border-b border-amber-200/60 bg-amber-50/90 px-6 py-2 backdrop-blur supports-[backdrop-filter]:bg-amber-50/80 dark:border-amber-500/20 dark:bg-amber-950/40">
      <div className="flex max-w-7xl items-center gap-3">
        <BellRing size={16} className="shrink-0 text-amber-600 dark:text-amber-400" />
        <p className="min-w-0 flex-1 truncate text-[13px] text-amber-900 dark:text-amber-100">
          {t('notification.banner', { count: critical.length })}
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={openNotifications}
          className="shrink-0 border-amber-300/60 bg-transparent text-amber-900 hover:bg-amber-100 dark:border-amber-500/30 dark:text-amber-100 dark:hover:bg-amber-900/40"
        >
          {t('notification.bannerCta')}
        </Button>
        <button
          onClick={dismiss}
          className="shrink-0 rounded-md p-1 text-amber-700 transition-colors hover:bg-amber-100 dark:text-amber-300 dark:hover:bg-amber-900/40"
          aria-label={t('notification.dismiss')}
          title={t('notification.dismiss')}
        >
          <X size={15} />
        </button>
      </div>
    </div>
  )
}
