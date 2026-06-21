#!/usr/bin/env node

import { createServer } from "node:http";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { randomBytes } from "node:crypto";

const SCOPES = {
  drive: [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/calendar",
  ],
  youtube: ["https://www.googleapis.com/auth/youtube.force-ssl"],
};

function usage(message) {
  if (message) console.error(`Error: ${message}\n`);
  console.error(
    "Usage: npm run oauth -- --identity <label> --service <drive|youtube> " +
      "[--credentials scripts/client_secret.json] [--output scripts/google_tokens.json]",
  );
  process.exit(1);
}

function parseArgs(argv) {
  const result = {};
  for (let i = 0; i < argv.length; i += 2) {
    const key = argv[i];
    const value = argv[i + 1];
    if (!key?.startsWith("--") || !value) usage(`Invalid argument near ${key || "end of command"}`);
    result[key.slice(2)] = value;
  }
  return result;
}

function openBrowser(url) {
  const commands = {
    win32: ["cmd", ["/c", "start", "", url]],
    darwin: ["open", [url]],
    linux: ["xdg-open", [url]],
  };
  const command = commands[process.platform];
  if (!command) return;
  const child = spawn(command[0], command[1], { detached: true, stdio: "ignore" });
  child.on("error", () => {});
  child.unref();
}

async function readJson(path) {
  return JSON.parse(await readFile(path, "utf8"));
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const identity = args.identity?.toLowerCase().trim();
  const service = args.service?.toLowerCase();
  const credentialsPath = resolve(args.credentials || "scripts/client_secret.json");
  const outputPath = resolve(args.output || "scripts/google_tokens.json");

  if (!identity || !/^[a-z0-9][a-z0-9_-]*$/.test(identity)) {
    usage("Identity must use lowercase letters, numbers, underscores, or hyphens.");
  }
  if (!SCOPES[service]) usage("Service must be drive or youtube.");

  const credentialsJson = await readJson(credentialsPath);
  const credentials = credentialsJson.installed || credentialsJson.web;
  if (!credentials?.client_id || !credentials?.client_secret) {
    usage("Credentials JSON does not contain an installed or web OAuth client.");
  }

  const state = randomBytes(24).toString("hex");
  let finish;
  const callback = new Promise((resolveCallback, rejectCallback) => {
    finish = { resolve: resolveCallback, reject: rejectCallback };
  });

  const server = createServer((request, response) => {
    const url = new URL(request.url || "/", "http://localhost");
    if (url.pathname !== "/oauth2callback") {
      response.writeHead(404).end("Not found");
      return;
    }
    if (url.searchParams.get("state") !== state) {
      response.writeHead(400).end("State mismatch. Close this window and try again.");
      finish.reject(new Error("OAuth state mismatch"));
      return;
    }
    const error = url.searchParams.get("error");
    const code = url.searchParams.get("code");
    if (error || !code) {
      response.writeHead(400).end("Google authorization failed. You can close this window.");
      finish.reject(new Error(error || "Google returned no authorization code"));
      return;
    }
    response.writeHead(200, { "Content-Type": "text/plain; charset=utf-8" });
    response.end("Authorization complete. You can close this window and return to the terminal.");
    finish.resolve(code);
  });

  await new Promise((resolveListen, rejectListen) => {
    server.once("error", rejectListen);
    server.listen(0, "127.0.0.1", resolveListen);
  });

  const address = server.address();
  const redirectUri = `http://127.0.0.1:${address.port}/oauth2callback`;
  const authUrl = new URL("https://accounts.google.com/o/oauth2/v2/auth");
  authUrl.search = new URLSearchParams({
    client_id: credentials.client_id,
    redirect_uri: redirectUri,
    response_type: "code",
    scope: SCOPES[service].join(" "),
    access_type: "offline",
    prompt: "consent",
    include_granted_scopes: "true",
    state,
  }).toString();

  console.log(`\nAuthorize the '${identity}' identity for ${service}:\n${authUrl}\n`);
  openBrowser(authUrl.toString());

  const timeout = setTimeout(() => finish.reject(new Error("OAuth timed out after 10 minutes")), 600_000);
  let code;
  try {
    code = await callback;
  } finally {
    clearTimeout(timeout);
    server.close();
  }

  const tokenResponse = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: credentials.client_id,
      client_secret: credentials.client_secret,
      code,
      grant_type: "authorization_code",
      redirect_uri: redirectUri,
    }),
  });
  const token = await tokenResponse.json();
  if (!tokenResponse.ok) throw new Error(`Token exchange failed: ${JSON.stringify(token)}`);
  if (!token.refresh_token) {
    throw new Error("Google did not return a refresh token. Revoke this app's access in your Google account, then retry.");
  }

  let output = {};
  try {
    output = await readJson(outputPath);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  output[identity] ||= { services: {} };
  output[identity].services ||= {};
  output[identity].services[service] = { refresh_token: token.refresh_token };

  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify(output, null, 2)}\n`, { mode: 0o600 });
  console.log(`Saved ${service} refresh token for '${identity}' to ${outputPath}`);
  console.log(`OAuth client ID: ${credentials.client_id}`);
  console.log("Keep the credentials and token files private; both are ignored by git.");
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
