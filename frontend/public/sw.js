// Talismã service worker — enables Web Push notifications on mobile/desktop.
// Served from /sw.js (Vite `public/`). The `push` handler shows the payload
// sent by the backend; tapping a notification focuses the app and navigates
// to the deep link carried in `data.url`.

const CACHE_NAME = 'talisma-core-v1'

self.addEventListener('install', (event) => {
  self.skipWaiting()
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) =>
        cache.addAll(['/', '/manifest.json', '/favicon.ico']),
      )
      .catch(() => {}),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))),
      )
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('push', (event) => {
  let payload = {}
  try {
    const text = event.data ? event.data.text() : ''
    payload = text ? JSON.parse(text) : {}
  } catch {
    payload = { title: 'Talismã', body: 'Novo alerta' }
  }

  const { title = 'Talismã', body = '', data = {}, icon = '/android-icon-192x192.png', badge = '/badge-icon-96x96.png' } = payload

  const options = {
    body,
    icon,
    badge,
    vibrate: [100, 50, 100],
    data,
  }

  event.waitUntil(self.registration.showNotification(title, options))
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()

  const url = event.notification.data?.url || '/'
  const openClient = () =>
    self.clients
      .matchAll({ type: 'window', includeUncontrolled: true })
      .then((clients) => {
        for (const client of clients) {
          if ('focus' in client) {
            client.navigate(url)
            return client.focus()
          }
        }
        return null
      })

  event.waitUntil(
    openClient().then((client) => {
      if (client) return
      return self.clients.openWindow(url)
    }),
  )
})
