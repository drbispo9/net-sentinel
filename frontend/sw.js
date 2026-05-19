/**
 * NetSentinel — Service Worker
 * Handles notification click events to reliably focus/open the dashboard.
 */

self.addEventListener('notificationclick', function(event) {
  event.notification.close();

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(clientList) {
      // If a NetSentinel tab already exists, focus it
      for (const client of clientList) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          return client.focus();
        }
      }
      // No existing tab — open a new one
      if (clients.openWindow) {
        return clients.openWindow('/');
      }
    })
  );
});
