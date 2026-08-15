// WhatsApp bridge for agent-core — unofficial, via Baileys (WhatsApp Web
// multi-device protocol). No Meta Business account or app review needed:
// log in once by scanning a QR code, session is cached in ./auth after that.
//
// Talks to the Python agent over plain localhost HTTP:
//   - inbound WhatsApp messages -> POST AGENT_WEBHOOK_URL (agent's /webhooks/whatsapp)
//   - outbound replies         <- POST /send on this process, called by WhatsAppChannel
//
// Config via env vars (see .env.example in this folder):
//   BRIDGE_PORT        port this server listens on           (default 8098)
//   BRIDGE_SECRET       shared bearer secret with the agent    (default: none — insecure, dev only)
//   BRIDGE_AUTH_DIR     where Baileys caches the login session (default ./auth)
//   AGENT_WEBHOOK_URL   the agent's inbound webhook            (default http://127.0.0.1:8099/webhooks/whatsapp)

import 'dotenv/config';
import express from 'express';
import pino from 'pino';
import qrcode from 'qrcode-terminal';
import makeWASocket, { DisconnectReason, useMultiFileAuthState } from '@whiskeysockets/baileys';

const PORT = Number(process.env.BRIDGE_PORT || 8098);
const SECRET = process.env.BRIDGE_SECRET || '';
const AUTH_DIR = process.env.BRIDGE_AUTH_DIR || './auth';
const AGENT_WEBHOOK_URL = process.env.AGENT_WEBHOOK_URL || 'http://127.0.0.1:8099/webhooks/whatsapp';

if (!SECRET) {
  console.warn(
    '[bridge] BRIDGE_SECRET is empty — anyone who can reach this port can send/receive ' +
      'through your WhatsApp. Fine for local dev, set it for anything else.'
  );
}

function authHeaders() {
  return SECRET
    ? { 'Content-Type': 'application/json', Authorization: `Bearer ${SECRET}` }
    : { 'Content-Type': 'application/json' };
}

async function forwardInbound(from, text, id) {
  try {
    const res = await fetch(AGENT_WEBHOOK_URL, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ from, text, id }),
    });
    if (!res.ok) {
      console.error(`[bridge] agent rejected inbound message: HTTP ${res.status}`);
    }
  } catch (err) {
    console.error('[bridge] failed to forward message to agent:', err.message);
  }
}

// Holds the live socket so the HTTP handlers below always see the current
// connection, even after a reconnect swaps it out.
let sock = null;

async function connectToWhatsApp() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  sock = makeWASocket({ auth: state, logger: pino({ level: 'silent' }) });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;
    if (qr) {
      console.log('[bridge] Scan this QR code with WhatsApp (Linked devices):');
      qrcode.generate(qr, { small: true });
    }
    if (connection === 'close') {
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const loggedOut = statusCode === DisconnectReason.loggedOut;
      console.log(
        loggedOut
          ? '[bridge] Logged out. Delete the auth/ folder and restart to scan a new QR code.'
          : '[bridge] Connection dropped, reconnecting...'
      );
      // Only reconnect the WhatsApp socket — never re-run the HTTP server,
      // it's already listening and re-binding the same port would crash
      // with EADDRINUSE.
      if (!loggedOut) connectToWhatsApp();
    } else if (connection === 'open') {
      console.log('[bridge] Connected to WhatsApp.');
    }
  });

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;
    for (const msg of messages) {
      if (!msg.message || msg.key.fromMe) continue;
      const text = msg.message.conversation || msg.message.extendedTextMessage?.text;
      if (!text) continue;
      const from = (msg.key.remoteJid || '').replace('@s.whatsapp.net', '');
      if (!from) continue;
      await forwardInbound(from, text, msg.key.id);
    }
  });
}

function startServer() {
  const app = express();
  app.use(express.json());

  app.use((req, res, next) => {
    if (!SECRET) return next();
    if (req.headers.authorization !== `Bearer ${SECRET}`) {
      return res.status(403).json({ ok: false, error: 'forbidden' });
    }
    next();
  });

  app.get('/health', (_req, res) => {
    res.json({ ok: true, connected: Boolean(sock?.user) });
  });

  app.post('/send', async (req, res) => {
    const { to, text } = req.body || {};
    if (!to || !text) {
      return res.status(400).json({ ok: false, error: 'missing to/text' });
    }
    if (!sock) {
      return res.status(503).json({ ok: false, error: 'not connected to whatsapp yet' });
    }
    try {
      const jid = to.includes('@') ? to : `${to}@s.whatsapp.net`;
      await sock.sendMessage(jid, { text });
      res.json({ ok: true });
    } catch (err) {
      res.status(500).json({ ok: false, error: String(err) });
    }
  });

  app.listen(PORT, '127.0.0.1', () => {
    console.log(`[bridge] listening on http://127.0.0.1:${PORT}`);
  });
}

startServer();
connectToWhatsApp();
