// app/static/sw-vistoria.js
//
// [FEATURE] vistoria offline — fiscal trabalha em condomínio sem sinal (ou com
// 3G/4G instável). Este service worker faz duas coisas:
//
//   1. Background Sync: reenvia a fila de vistorias guardadas no IndexedDB
//      (app/static/js/vistoria-offline.js) quando o Android avisa que a
//      internet voltou — mesmo com a aba fechada.
//
//   2. App shell offline: pré-cacheia os assets estáticos (JS/ícones/CSS) e
//      responde navegações com network-first + fallback pro cache. Sem isso,
//      recarregar a página do fiscal já sem sinal dava tela em branco e o
//      preenchimento só sobrevivia se a aba nunca fosse fechada.
//
// [FIX] escopo: este arquivo é servido em `/sw-vistoria.js` (rota dedicada em
// app/__init__.py, com header Service-Worker-Allowed: /) e registrado com
// scope '/'. Servido de /static/ o escopo default seria /static/ e o SW não
// controlaria nenhuma página (navigator.serviceWorker.ready nunca resolvia →
// reg.sync.register() nunca rodava → Background Sync morto).

importScripts('/static/js/vistoria-offline.js');

// Bump quando mudar a lista de assets abaixo ou a estratégia de cache.
var CACHE_NOME   = 'condoreservas-fiscal-shell-v1';
var SHELL_ASSETS = [
  '/static/js/vistoria-offline.js',
  '/static/js/signature_pad.umd.min.js',
  '/static/favicon.svg',
];

self.addEventListener('install', function (event) {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NOME).then(function (cache) {
      // {cache:'reload'} — nunca pega do HTTP cache do browser no precache.
      return cache.addAll(SHELL_ASSETS.map(function (u) {
        return new Request(u, { cache: 'reload' });
      })).catch(function () { /* asset novo/renomeado — não trava a instalação */ });
    })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (nomes) {
      return Promise.all(nomes.map(function (n) {
        if (n !== CACHE_NOME) return caches.delete(n);
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

// Estratégias:
//  - navegação (documento HTML): network-first, cai no cache se offline.
//    Guarda a última versão boa de cada URL pra ter fallback.
//  - GET estático same-origin: stale-while-revalidate (responde do cache na
//    hora, atualiza em segundo plano).
//  - resto (POST de vistoria, APIs): passa direto, sem tocar.
self.addEventListener('fetch', function (event) {
  var req = event.request;
  if (req.method !== 'GET') return;

  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).then(function (res) {
        if (res && res.ok) {
          var copia = res.clone();
          caches.open(CACHE_NOME).then(function (c) { c.put(req, copia); });
        }
        return res;
      }).catch(function () {
        return caches.match(req).then(function (hit) {
          return hit || caches.match('/fiscal/painel') || Response.error();
        });
      })
    );
    return;
  }

  if (url.pathname.indexOf('/static/') === 0) {
    event.respondWith(
      caches.open(CACHE_NOME).then(function (cache) {
        return cache.match(req).then(function (hit) {
          var rede = fetch(req).then(function (res) {
            if (res && res.ok) cache.put(req, res.clone());
            return res;
          }).catch(function () { return hit; });
          return hit || rede;
        });
      })
    );
  }
});

self.addEventListener('sync', function (event) {
  if (event.tag === 'vistoria-sync') {
    event.waitUntil(flushPendentes());
  }
});
