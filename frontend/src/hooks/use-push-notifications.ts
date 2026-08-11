import { useCallback, useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { push as pushApi } from '@/lib/api'

export type PushStatus = 'unsupported' | 'idle' | 'default' | 'denied' | 'enabled'

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4)
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/')
  const rawData = window.atob(base64)
  const outputArray = new Uint8Array(rawData.length)
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i)
  }
  return outputArray
}

function toArrayBuffer(value: string): ArrayBuffer {
  const bytes = urlBase64ToUint8Array(value)
  return bytes.buffer.slice(0) as ArrayBuffer
}

function detectDeviceLabel(): string {
  const ua = navigator.userAgent
  let os = 'device'
  if (/Android/.test(ua)) os = 'Android'
  else if (/iPhone|iPad|iPod/.test(ua)) os = 'iOS'
  else if (/Windows/.test(ua)) os = 'Windows'
  else if (/Mac OS X/.test(ua)) os = 'macOS'
  else if (/Linux/.test(ua)) os = 'Linux'
  return `${os} · Chrome`
}

const SW_READY_TIMEOUT_MS = 8000

function withTimeout<T>(promise: Promise<T>, ms: number, onTimeout: string): Promise<T> {
  return Promise.race([
    promise,
    new Promise<never>((_, reject) =>
      setTimeout(() => reject(new Error(onTimeout)), ms),
    ),
  ])
}

/**
 * Web Push (VAPID) subscription lifecycle for the current browser/device.
 * The backend stays the source of truth for the active subscription id, so
 * unsubscribing removes the row server-side too.
 */
export function usePushNotifications() {
  const { t } = useTranslation()
  const [status, setStatus] = useState<PushStatus>('idle')
  const [subscriptionId, setSubscriptionId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [lastError, setLastError] = useState<string | null>(null)

  const { data: vapid } = useQuery({
    queryKey: ['push-vapid-key'],
    queryFn: pushApi.vapidKey,
    staleTime: Infinity,
  })

  const supported =
    typeof window !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    !!vapid?.enabled

  const syncFromBrowser = useCallback(async () => {
    if (!supported) {
      setStatus('unsupported')
      return
    }
    try {
      const registration = await navigator.serviceWorker.ready
      const sub = await registration.pushManager.getSubscription()
      if (!sub) {
        setStatus(Notification.permission === 'denied' ? 'denied' : 'idle')
        setSubscriptionId(null)
        return
      }
      const registered = await pushApi.list()
      const known = registered.some((s) => s.endpoint === sub.endpoint)
      if (!known) {
        // The backend no longer has this endpoint (e.g. it was pruned as dead).
        // Re-register the existing browser subscription server-side instead of
        // churning the browser token (unsubscribe→subscribe is flaky on Chrome
        // Android and fails silently).
        const info = await pushApi.subscribe(
          sub.endpoint,
          sub.toJSON().keys as { p256dh: string; auth: string },
          detectDeviceLabel(),
        )
        setSubscriptionId(info.id)
      }
      setStatus('enabled')
    } catch (err) {
      console.error('[push] syncFromBrowser failed', err)
      setStatus('idle')
    }
  }, [supported])

  useEffect(() => {
    void syncFromBrowser()
  }, [syncFromBrowser])

  const enable = useCallback(async (): Promise<boolean> => {
    if (!supported || !vapid) return false
    setBusy(true)
    setLastError(null)
    try {
      const permission = await Notification.requestPermission()
      if (permission === 'denied') {
        setStatus('denied')
        setLastError(t('notification.pushPermissionBlocked'))
        return false
      }
      if (permission === 'default') {
        setStatus('default')
        setLastError(t('notification.pushPermDefault'))
        return false
      }

      const registration = await withTimeout(
        navigator.serviceWorker.ready,
        SW_READY_TIMEOUT_MS,
        t('notification.pushSwTimeout'),
      )

      // Reuse an existing browser subscription when present — only create a
      // fresh one if there is none. Never unsubscribe first: that pattern is
      // unreliable on Android and, when it fails, leaves push silently broken.
      let sub = await registration.pushManager.getSubscription()
      if (!sub) {
        sub = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: toArrayBuffer(vapid.public_key),
        })
      }

      const info = await pushApi.subscribe(
        sub.endpoint,
        sub.toJSON().keys as { p256dh: string; auth: string },
        detectDeviceLabel(),
      )
      setSubscriptionId(info.id)
      setStatus('enabled')
      return true
    } catch (err) {
      console.error('[push] enable failed', err)
      setStatus('idle')
      setLastError(err instanceof Error ? err.message : t('notification.pushActivateError'))
      return false
    } finally {
      setBusy(false)
    }
  }, [supported, vapid, t])

  const disable = useCallback(async (): Promise<void> => {
    if (!supported) return
    setBusy(true)
    setLastError(null)
    try {
      const registration = await navigator.serviceWorker.ready
      const sub = await registration.pushManager.getSubscription()
      if (sub) await sub.unsubscribe()
      if (subscriptionId) {
        pushApi.unsubscribe(subscriptionId).catch(() => {})
      }
    } finally {
      setSubscriptionId(null)
      setStatus(Notification.permission === 'denied' ? 'denied' : 'idle')
      setBusy(false)
    }
  }, [supported, subscriptionId])

  return { supported, enabled: status === 'enabled', status, busy, lastError, enable, disable }
}
