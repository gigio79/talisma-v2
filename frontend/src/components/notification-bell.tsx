import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { notifications as notificationsApi } from '@/lib/api'
import { toast } from 'sonner'
import {
  Bell,
  BellRing,
  CalendarClock,
  CheckCheck,
  Clock,
  TriangleAlert,
  Trash2,
} from 'lucide-react'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Badge } from '@/components/ui/badge'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useWorkspace } from '@/contexts/workspace-context'
import type { Notification, NotificationAlertType } from '@/types'
import { cn } from '@/lib/utils'

function formatCurrency(value: number, currency: string, locale: string) {
  return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(value)
}

const ALERT_META: Record<
  NotificationAlertType,
  { icon: typeof Clock; labelKey: string; tone: string }
> = {
  '7_DAYS': { icon: Clock, labelKey: 'notification.sevenDays', tone: 'text-muted-foreground' },
  '3_DAYS': { icon: CalendarClock, labelKey: 'notification.threeDays', tone: 'text-amber-500' },
  '1_DAY': { icon: TriangleAlert, labelKey: 'notification.oneDay', tone: 'text-orange-500' },
  DUE_DATE: { icon: BellRing, labelKey: 'notification.dueDate', tone: 'text-rose-500' },
}

function daysUntil(dueDate: string): number {
  const due = new Date(dueDate + 'T00:00:00')
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Math.round((due.getTime() - today.getTime()) / 86400000)
}

export function NotificationBell({ dark = false }: { dark?: boolean }) {
  const { t } = useTranslation()
  const { canWrite } = useWorkspace()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['notifications'] })

  const { data: count } = useQuery({
    queryKey: ['notifications', 'unread-count'],
    queryFn: notificationsApi.unreadCount,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  })

  const { data: items } = useQuery({
    queryKey: ['notifications'],
    queryFn: () => notificationsApi.list(),
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  })

  const markReadMutation = useMutation({
    mutationFn: notificationsApi.markRead,
    onSuccess: invalidate,
    onError: () => toast.error(t('common.error')),
  })

  const dismissMutation = useMutation({
    mutationFn: notificationsApi.dismiss,
    onSuccess: invalidate,
    onError: () => toast.error(t('common.error')),
  })

  const readAllMutation = useMutation({
    mutationFn: notificationsApi.markAllRead,
    onSuccess: invalidate,
    onError: () => toast.error(t('common.error')),
  })

  // The dismissible banner can ask the bell to open.
  useEffect(() => {
    const handler = () => setOpen(true)
    window.addEventListener('talisma:open-notifications', handler)
    return () => window.removeEventListener('talisma:open-notifications', handler)
  }, [])

  const unread = items?.filter((n) => n.status === 'unread') ?? []
  const notifications = items ?? []

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <button
          className={cn(
            'relative p-1 rounded-md transition-colors',
            dark
              ? 'text-sidebar-muted hover:text-sidebar-foreground'
              : 'text-muted-foreground hover:text-foreground hover:bg-muted',
          )}
          title={t('notification.title')}
          aria-label={t('notification.title')}
        >
          <Bell size={18} />
          {(count?.count ?? 0) > 0 && (
            <Badge
              className="absolute -top-0.5 -right-0.5 h-4 min-w-4 px-1 text-[10px] leading-none"
              variant="destructive"
            >
              {count!.count > 99 ? '99+' : count!.count}
            </Badge>
          )}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80 max-h-[70vh] overflow-y-auto">
        <DropdownMenuLabel className="flex items-center justify-between pr-2">
          <span>{t('notification.title')}</span>
          {unread.length > 0 && (
            <Badge variant="secondary" className="font-normal">
              {t('notification.unreadCount', { count: unread.length })}
            </Badge>
          )}
        </DropdownMenuLabel>
        {notifications.length === 0 ? (
          <div className="px-3 py-6 text-center text-sm text-muted-foreground">
            {t('notification.empty')}
          </div>
        ) : (
          <>
            <DropdownMenuSeparator />
            {notifications.map((n) => (
              <NotificationRow
                key={n.id}
                notification={n}
                canWrite={canWrite}
                onRead={() => markReadMutation.mutate(n.id)}
                onDismiss={() => dismissMutation.mutate(n.id)}
              />
            ))}
            {unread.length > 0 && canWrite && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  className="gap-2 cursor-pointer"
                  onSelect={() => readAllMutation.mutate()}
                >
                  <CheckCheck size={14} />
                  {t('notification.markAllRead')}
                </DropdownMenuItem>
              </>
            )}
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function NotificationRow({
  notification: n,
  canWrite,
  onRead,
  onDismiss,
}: {
  notification: Notification
  canWrite: boolean
  onRead: () => void
  onDismiss: () => void
}) {
  const { t, i18n } = useTranslation()
  const { mask } = usePrivacyMode()
  const locale = i18n.resolvedLanguage ?? i18n.language
  const meta = ALERT_META[n.alert_type]
  const Icon = meta.icon
  const title = n.description ?? n.account_name ?? t('notification.title')
  const amount = n.amount != null ? `${formatCurrency(n.amount, n.currency ?? 'BRL', locale)}` : ''
  const dueIn = daysUntil(n.due_date)

  return (
    <div
      className={cn(
        'px-3 py-2.5 flex items-start gap-3 hover:bg-muted/50',
        n.status === 'unread' && 'bg-primary/5',
      )}
    >
      <Icon size={16} className={cn('mt-0.5 shrink-0', meta.tone)} />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-foreground truncate">
          {mask(title)}
          {amount && (
            <span className={cn('ml-1.5 text-xs font-semibold', n.type === 'credit' ? 'text-emerald-600' : 'text-rose-500')}>
              {mask(amount)}
            </span>
          )}
        </p>
        <p className="text-xs text-muted-foreground">
          {t(meta.labelKey)}
          <span className="mx-1">·</span>
          {dueIn >= 0
            ? new Date(n.due_date + 'T00:00:00').toLocaleDateString(locale)
            : t('notification.overdue', { date: new Date(n.due_date + 'T00:00:00').toLocaleDateString(locale) })}
        </p>
      </div>
      {canWrite && n.status === 'unread' && (
        <div className="flex shrink-0 items-center gap-0.5">
          <button
            className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            onClick={onRead}
            title={t('notification.markRead')}
            aria-label={t('notification.markRead')}
          >
            <CheckCheck size={14} />
          </button>
          <button
            className="p-1 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 transition-colors"
            onClick={onDismiss}
            title={t('notification.dismiss')}
            aria-label={t('notification.dismiss')}
          >
            <Trash2 size={14} />
          </button>
        </div>
      )}
    </div>
  )
}
