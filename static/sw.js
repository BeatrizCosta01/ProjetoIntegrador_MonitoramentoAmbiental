self.addEventListener('push', event => {
    let dados = {
        title: 'Alerta Data Center NOC',
        body: 'Violação de parâmetro ambiental detectada.'
    };

    if (event.data) {
        try {
            dados = event.data.json();
        } catch (e) {
            dados.body = event.data.text();
        }
    }

    event.waitUntil(
        self.registration.showNotification(dados.title, {
            body: dados.body,
            tag: 'datacenter-alerta',
            requireInteraction: true
        })
    );
});

self.addEventListener('notificationclick', event => {
    event.notification.close();
    event.waitUntil(
        clients.openWindow('/dashboard')
    );
});