import { Container } from "@cloudflare/containers";

export class ProxyContainer extends Container {
  defaultPort = 8080;
  sleepAfter = "5m";
}

export default {
  async fetch(request, env) {
    // Require a shared secret so the MCP endpoint isn't open to the internet.
    // Set the secret with: npx wrangler secret put MCP_AUTH_TOKEN
    // Then pass it from the client as a Bearer token or ?token= query param.
    const authToken = env.MCP_AUTH_TOKEN;
    if (authToken) {
      const authHeader = request.headers.get("Authorization") || "";
      const url = new URL(request.url);
      const queryToken = url.searchParams.get("token") || "";
      const valid =
        authHeader === `Bearer ${authToken}` || queryToken === authToken;
      if (!valid) {
        return new Response(JSON.stringify({ error: "Unauthorized" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        });
      }
    }

    const container = env.PROXY_CONTAINER.getByName("main");
    return container.fetch(request);
  },
};
