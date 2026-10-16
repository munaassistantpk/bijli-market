self.addEventListener('push', function(e) {
  var d = {title: 'BijliMarket', body: '', url: '/'};
  try { d = Object.assign(d, e.data.json()); } catch (err) {}
  e.waitUntil(self.registration.showNotification(d.title, {
    body: d.body, icon: '/static/icon.png', data: {url: d.url}
  }));
});
self.addEventListener('notificationclick', function(e) {
  e.notification.close();
  e.waitUntil(clients.openWindow(e.notification.data.url || '/'));
});
