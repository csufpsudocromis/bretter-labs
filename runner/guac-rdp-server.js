#!/usr/bin/env node
"use strict";

const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const net = require("net");
const path = require("path");
const { URL } = require("url");

const GuacamoleLite = require("guacamole-lite");

const listenHost = process.env.GUAC_LISTEN_HOST || "0.0.0.0";
const listenPort = parseInt(process.env.GUAC_HTTP_PORT || process.env.WS_PORT || "6080", 10);
const tunnelPath = process.env.GUAC_TUNNEL_PATH || "/rdp-tunnel";
const webRoot = process.env.GUAC_WEB_ROOT || "/opt/runner/guac-web";

const guacdHost = process.env.GUACD_HOST || "127.0.0.1";
const guacdPort = parseInt(process.env.GUACD_PORT || "4822", 10);

const rdpHost = process.env.GUAC_RDP_HOST || "127.0.0.1";
const rdpPort = parseInt(process.env.GUAC_RDP_PORT || "33890", 10);
const rdpSecurity = process.env.GUAC_RDP_SECURITY || "any";
const rdpIgnoreCert = String(process.env.GUAC_RDP_IGNORE_CERT || "true").toLowerCase() !== "false";
const maxInactivityMsRaw = parseInt(process.env.GUAC_MAX_INACTIVITY_MS || "0", 10);
const maxInactivityMs = Number.isFinite(maxInactivityMsRaw) && maxInactivityMsRaw >= 0 ? maxInactivityMsRaw : 0;

const cryptSeed = process.env.GUAC_TOKEN_KEY || "bretter-labs-guac-rdp";
const cryptCipher = "aes-256-cbc";
const cryptKey = crypto.createHash("sha256").update(cryptSeed).digest();

const mimeTypes = {
  ".css": "text/css; charset=utf-8",
  ".gif": "image/gif",
  ".html": "text/html; charset=utf-8",
  ".jpg": "image/jpeg",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
};

function sendJson(res, statusCode, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(statusCode, {
    "cache-control": "no-store",
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(body),
  });
  res.end(body);
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let total = 0;
    req.on("data", (chunk) => {
      total += chunk.length;
      if (total > 16 * 1024) {
        reject(new Error("request body too large"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      if (!chunks.length) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
      } catch (err) {
        reject(new Error("invalid JSON payload"));
      }
    });
    req.on("error", reject);
  });
}

function coerceString(value, maxLength = 128) {
  const text = String(value || "").trim();
  return text.slice(0, maxLength);
}

function buildConnectionSettings(payload) {
  const username = coerceString(payload.username, 128);
  const password = coerceString(payload.password, 256);
  const domain = coerceString(payload.domain, 128);
  const initialProgram = coerceString(payload.initial_program, 256);

  const settings = {
    "hostname": rdpHost,
    "port": String(rdpPort),
    "security": rdpSecurity,
    "ignore-cert": rdpIgnoreCert ? "true" : "false",
    "enable-wallpaper": "false",
    "enable-theming": "false",
    "enable-full-window-drag": "false",
    "enable-desktop-composition": "false",
    "enable-menu-animations": "false",
  };

  if (username) settings.username = username;
  if (password) settings.password = password;
  if (domain) settings.domain = domain;
  if (initialProgram) settings["initial-program"] = initialProgram;
  return settings;
}

function encryptToken(payload) {
  const iv = crypto.randomBytes(16);
  const cipher = crypto.createCipheriv(cryptCipher, cryptKey, iv);
  const encrypted = Buffer.concat([cipher.update(JSON.stringify(payload), "utf8"), cipher.final()]);
  return Buffer.from(
    JSON.stringify({
      iv: iv.toString("base64"),
      value: encrypted.toString("base64"),
    }),
    "utf8"
  ).toString("base64");
}

function serveStatic(req, res) {
  const requestUrl = new URL(req.url, "http://localhost");
  const reqPath = requestUrl.pathname === "/" ? "/rdp.html" : requestUrl.pathname;
  const decodedPath = decodeURIComponent(reqPath);
  const safePath = path.normalize(decodedPath).replace(/^(\.\.[/\\])+/, "");
  const targetPath = path.join(webRoot, safePath);
  const resolvedRoot = path.resolve(webRoot);
  const resolvedTarget = path.resolve(targetPath);
  if (!(resolvedTarget === resolvedRoot || resolvedTarget.startsWith(`${resolvedRoot}${path.sep}`))) {
    res.writeHead(403, { "content-type": "text/plain; charset=utf-8" });
    res.end("forbidden");
    return;
  }
  fs.readFile(resolvedTarget, (err, data) => {
    if (err) {
      res.writeHead(err.code === "ENOENT" ? 404 : 500, { "content-type": "text/plain; charset=utf-8" });
      res.end(err.code === "ENOENT" ? "not found" : "error");
      return;
    }
    const ext = path.extname(resolvedTarget).toLowerCase();
    res.writeHead(200, {
      "cache-control": "no-store",
      "content-type": mimeTypes[ext] || "application/octet-stream",
      "content-length": data.length,
    });
    res.end(data);
  });
}

function checkRdpReady(timeoutMs = 1000) {
  return new Promise((resolve) => {
    let settled = false;
    const socket = net.createConnection({ host: rdpHost, port: rdpPort });
    const finish = (ready) => {
      if (settled) return;
      settled = true;
      try {
        socket.destroy();
      } catch (_err) {}
      resolve(Boolean(ready));
    };
    socket.once("connect", () => finish(true));
    socket.once("error", () => finish(false));
    socket.setTimeout(timeoutMs, () => finish(false));
  });
}

const server = http.createServer(async (req, res) => {
  const requestUrl = new URL(req.url, "http://localhost");
  if (requestUrl.pathname === "/rdp-ready") {
    if (req.method !== "GET") {
      sendJson(res, 405, { detail: "method not allowed" });
      return;
    }
    const ready = await checkRdpReady();
    sendJson(res, 200, { ready });
    return;
  }
  if (requestUrl.pathname === "/rdp-token") {
    try {
      let payload = {};
      if (req.method === "POST") {
        payload = await readJsonBody(req);
      } else if (req.method !== "GET") {
        sendJson(res, 405, { detail: "method not allowed" });
        return;
      }
      const tokenPayload = {
        connection: {
          type: "rdp",
          settings: buildConnectionSettings(payload),
        },
      };
      sendJson(res, 200, { token: encryptToken(tokenPayload) });
      return;
    } catch (err) {
      sendJson(res, 400, { detail: err.message || "invalid request" });
      return;
    }
  }
  serveStatic(req, res);
});

new GuacamoleLite(
  { server, path: tunnelPath },
  { host: guacdHost, port: guacdPort },
  {
    maxInactivityTime: maxInactivityMs,
    crypt: {
      cypher: cryptCipher,
      key: cryptKey,
    },
    log: {
      level: process.env.GUAC_LOG_LEVEL || "ERRORS",
      stdLog: console.log,
      errorLog: console.error,
    },
  },
  {
    processConnectionSettings: (settings, callback) => {
      try {
        const mergedSettings = Object.assign({}, settings?.connection?.settings || {});
        mergedSettings.hostname = rdpHost;
        mergedSettings.port = String(rdpPort);
        mergedSettings.security = rdpSecurity;
        mergedSettings["ignore-cert"] = rdpIgnoreCert ? "true" : "false";
        settings.connection = settings.connection || {};
        settings.connection.type = "rdp";
        settings.connection.settings = mergedSettings;
        callback(undefined, settings);
      } catch (err) {
        callback(err);
      }
    },
  }
);

server.listen(listenPort, listenHost, () => {
  console.log(
    `Guacamole RDP server listening on ${listenHost}:${listenPort}, tunnel=${tunnelPath}, target=${rdpHost}:${rdpPort}`
  );
});
