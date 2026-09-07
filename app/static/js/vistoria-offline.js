// app/static/js/vistoria-offline.js
//
// [FEATURE] fila offline pra vistoria digital do fiscal — sem sinal no
// condomínio, o POST do formulário falha e o fiscal perdia tudo que
// preencheu (checklist, fotos, assinaturas). Guarda no IndexedDB do celular
// e reenvia sozinho quando a internet voltar.
//
// Carregado tanto como <script> normal na página (vistoria_form.html,
// painel_fiscal.html) quanto via importScripts() dentro do service worker
// (sw-vistoria.js) — por isso não pode depender de `window`/`document`,
// só de APIs disponíveis nos dois contextos (indexedDB, fetch, FormData).

var VISTORIA_DB_NAME    = 'condoreservas-vistorias-offline';
var VISTORIA_DB_VERSION = 1;
var VISTORIA_STORE      = 'pendentes';

// [FIX] fila fantasma: quando o servidor RESPONDE recusando (400/500 — token
// CSRF vencido, dado inválido, vistoria já reivindicada), reenviar os mesmos
// bytes nunca vai funcionar. Antes isso ficava em loop infinito e o fiscal
// via "1 vistoria salva no celular" pra sempre, sem saber que ela nunca ia.
// Agora conta as recusas de servidor e, depois de MAX_TENTATIVAS_SERVIDOR,
// marca como `falhou` e para de tentar — o painel mostra isso em vermelho
// pro fiscal avisar o admin / anotar no papel. Falha de REDE (fetch nem
// completa) não conta: aí é só falta de sinal, continua tentando pra sempre.
var MAX_TENTATIVAS_SERVIDOR = 3;

function vistoriaAbrirDB() {
  return new Promise(function (resolve, reject) {
    var req = indexedDB.open(VISTORIA_DB_NAME, VISTORIA_DB_VERSION);
    req.onupgradeneeded = function () {
      req.result.createObjectStore(VISTORIA_STORE, { keyPath: 'id', autoIncrement: true });
    };
    req.onsuccess = function () { resolve(req.result); };
    req.onerror   = function () { reject(req.error); };
  });
}

// registro = { url, entries: [[nome, valor], ...], criadoEm, tentativas?, falhou? }
function vistoriaSalvarPendente(registro) {
  return vistoriaAbrirDB().then(function (db) {
    return new Promise(function (resolve, reject) {
      var tx = db.transaction(VISTORIA_STORE, 'readwrite');
      tx.objectStore(VISTORIA_STORE).add(registro);
      tx.oncomplete = function () { resolve(); };
      tx.onerror    = function () { reject(tx.error); };
    });
  });
}

function vistoriaAtualizarPendente(registro) {
  return vistoriaAbrirDB().then(function (db) {
    return new Promise(function (resolve, reject) {
      var tx = db.transaction(VISTORIA_STORE, 'readwrite');
      tx.objectStore(VISTORIA_STORE).put(registro);
      tx.oncomplete = function () { resolve(); };
      tx.onerror    = function () { reject(tx.error); };
    });
  });
}

function vistoriaListarPendentes() {
  return vistoriaAbrirDB().then(function (db) {
    return new Promise(function (resolve, reject) {
      var tx  = db.transaction(VISTORIA_STORE, 'readonly');
      var req = tx.objectStore(VISTORIA_STORE).getAll();
      req.onsuccess = function () { resolve(req.result || []); };
      req.onerror   = function () { reject(req.error); };
    });
  });
}

// Resumo pro banner do painel: quantas ainda vão tentar sozinhas x quantas
// desistiram (precisam de ação humana).
function vistoriaContarStatus() {
  return vistoriaListarPendentes().then(function (pendentes) {
    var aguardando = 0, falhou = 0;
    pendentes.forEach(function (p) {
      if (p.falhou) falhou++; else aguardando++;
    });
    return { aguardando: aguardando, falhou: falhou, total: pendentes.length };
  });
}

function vistoriaRemoverPendente(id) {
  return vistoriaAbrirDB().then(function (db) {
    return new Promise(function (resolve, reject) {
      var tx = db.transaction(VISTORIA_STORE, 'readwrite');
      tx.objectStore(VISTORIA_STORE).delete(id);
      tx.oncomplete = function () { resolve(); };
      tx.onerror    = function () { reject(tx.error); };
    });
  });
}

function vistoriaEnviarPendente(registro) {
  var fd = new FormData();
  registro.entries.forEach(function (par) { fd.append(par[0], par[1]); });
  return fetch(registro.url, { method: 'POST', body: fd, credentials: 'same-origin' });
}

// Tenta mandar cada pendência, em sequência (evita rajada de uploads de foto
// grande ao mesmo tempo num 4G ruim). Sucesso (2xx/redirect) → apaga.
// Recusa do servidor → conta a tentativa; ao bater o teto, marca `falhou` e
// nunca mais tenta sozinha. Erro de rede → deixa quieto pro próximo flush.
function flushPendentes() {
  return vistoriaListarPendentes().then(function (pendentes) {
    var seq = Promise.resolve();
    pendentes.forEach(function (p) {
      if (p.falhou) return; // desistiu — espera ação humana
      seq = seq.then(function () {
        return vistoriaEnviarPendente(p)
          .then(function (res) {
            if (res.ok || res.redirected) {
              return vistoriaRemoverPendente(p.id);
            }
            // Servidor respondeu recusando — reenviar igual não resolve.
            p.tentativas = (p.tentativas || 0) + 1;
            if (p.tentativas >= MAX_TENTATIVAS_SERVIDOR) p.falhou = true;
            return vistoriaAtualizarPendente(p);
          })
          .catch(function () { /* ainda sem internet — tenta no próximo flush */ });
      });
    });
    return seq;
  });
}
