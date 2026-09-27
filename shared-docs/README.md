# How the services connect

This folder contains instructions, not an application. There is nothing to
install or run here.

## Read these in order

1. Open `ui/README.md` in the full Sharing-MCPs collection and get one chat
   working. If you downloaded only cloud folders, you can instead connect a
   service to another MCP client.
2. Follow the [Qualia guide](../mind-backend/README.md) for persistent memory.
   Stop when its connection check works.
3. Follow the [Limbic guide](../limbic/README.md) for drive state. Use the same
   lowercase identity IDs that you chose for Qualia and the UI.
4. Read [CONNECTIONS.md](CONNECTIONS.md) to connect those services to a client.
   Its table identifies the external accounts, apps and services you must supply.
5. Use [SERVICE-MAP.md](SERVICE-MAP.md) to find which package owns each component.
6. Check [COVERAGE.md](COVERAGE.md) before adding phone, smart-home, browser or
   world features. It lists the optional custom components not packaged here yet.

## How to tell you are finished

Your chat client should list the tools from each service you enabled. Try one
small read-only tool from each before relying on it in a conversation. Deploying
a Worker and connecting it to your client are separate steps; complete both in
that package's README.

## If it does not connect

- Check the service URL and your own key against the package's example.
- Replace every `YOUR-...` value. Examples cannot authenticate on their own.
- Keep identity IDs consistent between services.
- Restart your client after changing its MCP configuration.
- Return to that package's success check before debugging the whole stack.

Commons is a connector to a separately hosted world server; it does not install
one. Accounts, model subscriptions, app installations, credentials and personal
content must be supplied by the installer.
