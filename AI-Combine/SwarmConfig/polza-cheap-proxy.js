#!/usr/bin/env node
/**
 * polza-cheap-proxy.js — прокси «самый дешёвый провайдер» для polza.ai
 *
 * Порт :8084. Клиент шлёт обычный OpenAI-запрос, прокси:
 *   1) подставляет Authorization: Bearer <ключ polza> (клиенту ключ не нужен)
 *   2) инжектит в body: provider.sort = "price" (если клиент не задал свой provider)
 *
 * Клиенты: ZCode (провайдер «openai»→baseUrl http://127.0.0.1:8084/api/v1),
 *          OpenWebUI (openai-соединение http://127.0.0.1:8084/v1)
 *
 * Поддержано: SSE-стриминг (stream:true) — прокси пайпит без буферизации;
 *          обычные ответы; GET /v1/models (проксируется как есть).
 * Не трогаем: embeddings, audio, media, responses — проходят как есть.
 */
'use strict';

const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');

const PORT = parseInt(process.env.POLZA_PROXY_PORT || '8084', 10);
const UPSTREAM_HOST = 'polza.ai';
const KEY_FILE = path.join(require('os').homedir(), 'AiStart', 'PolzaKey');
const FALLBACK_KEY_FILE = path.join(require('os').homedir(), 'AiStart', 'OpenRouteAi_Key');

function readKey() {
  // 1) env 2) ~/AiStart/PolzaKey 3) pza_* из v2/provider_config.json
  if (process.env.POLZA_API_KEY) return process.env.POLZA_API_KEY.trim();
  for (const f of [KEY_FILE, FALLBACK_KEY_FILE]) {
    try {
      const t = fs.readFileSync(f, 'utf8').trim();
      if (t) return t;
    } catch (_) { /* файла нет — ок */ }
  }
  try {
    const cfg = JSON.parse(fs.readFileSync(
      path.join(require('os').homedir(), '.zcode/v2/provider_config.json'), 'utf8'));
    const s = JSON.stringify(cfg);
    const m = s.match(/pza_[A-Za-z0-9_-]+/);
    if (m) return m[0];
  } catch (_) { /* нет файла — ок */ }
  return null;
}

/** Инжект provider.sort=price в JSON-body chat/completions/responses/messages. */
function injectCheapest(body) {
  if (!body || typeof body !== 'object') return body;
  const isChat = body.model && (
    body.messages || body.input || body.stream !== undefined
  );
  if (!isChat) return body;
  if (body.provider && typeof body.provider === 'object') {
    // клиент знает о routing — не мешаем, только дефолт сортировки
    if (!body.provider.sort) body.provider.sort = 'price';
    return body;
  }
  body.provider = { sort: 'price' };
  return body;
}

function mask(k) {
  return k ? (k.slice(0, 4) + '***' + k.slice(-4)) : '(нет)';
}

const server = http.createServer((req, res) => {
  const chunks = [];
  let bodyLen = 0;
  req.on('data', (c) => {
    chunks.push(c);
    bodyLen += c.length;
    if (bodyLen > 64 * 1024 * 1024) req.destroy(); // 64 МБ лимит
  });

  req.on('end', () => {
    const raw = Buffer.concat(chunks);
    const key = readKey();
    if (!key) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: { message: 'polza key not found (env POLZA_API_KEY | ~/AiStart/PolzaKey | provider_config.json)' } }));
      return;
    }

    let outBody = raw;
    const isJson = (req.headers['content-type'] || '').includes('json');
    let touched = false;
    if (isJson && raw.length) {
      try {
        const j = JSON.parse(raw.toString('utf8'));
        const before = JSON.stringify(j);
        injectCheapest(j);
        if (JSON.stringify(j) !== before) touched = true;
        outBody = Buffer.from(JSON.stringify(j), 'utf8');
      } catch (_) { /* не JSON — идёт как есть */ }
    }

    const headers = { ...req.headers };
    // hop-by-hop и сжатие: Node не декодирует gzip upstream-ответа — убираем, чтобы pipe был чистым
    delete headers.host;
    delete headers.connection;
    delete headers['keep-alive'];
    delete headers['transfer-encoding'];
    delete headers.upgrade;
    delete headers['proxy-authorization'];
    delete headers['accept-encoding'];
    delete headers['content-length'];
    if (touched) headers['content-length'] = Buffer.byteLength(outBody);
    headers.authorization = 'Bearer ' + key;
    headers.accept = req.headers.accept || '*/*';

    const upstreamReq = https.request({
      hostname: UPSTREAM_HOST,
      port: 443,
      path: req.url,
      method: req.method,
      headers,
    }, (upstreamRes) => {
      res.writeHead(upstreamRes.statusCode, upstreamRes.headers);
      upstreamRes.pipe(res); // SSE/стримы проходят прозрачно
    });

    upstreamReq.on('error', (e) => {
      console.error('UPSTREAM ERR:', e.code, e.message, e.cause && e.cause.code);
      if (!res.headersSent) {
        res.writeHead(502, { 'Content-Type': 'application/json' });
      }
      try { res.end(JSON.stringify({ error: { message: 'polza upstream error: ' + (e.code || '') + ' ' + e.message } })); } catch (_) {}
    });

    if (outBody.length) upstreamReq.write(outBody);
    upstreamReq.end();
    console.log(`${new Date().toISOString()} ${req.method} ${req.url} ${touched ? '[+price]' : '[as-is]'}`);
  });
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`polza-cheap-proxy on http://127.0.0.1:${PORT} -> https://polza.ai/api/v1 | key=${mask(readKey())}`);
});
