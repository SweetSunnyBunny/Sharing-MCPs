# 🧠 Mind

Memory, feelings and shared home state: the pieces that make a companion feel continuous from one day to the next.

| Piece | Runs on | What it does |
| --- | --- | --- |
| [hearth-commons](hearth-commons/README.md) | ☁️ your cloud | Remote MCP connector for a Commons server |
| [hearth-hub](hearth-hub/README.md) | ☁️ your cloud | Shared state and workspace service |
| [limbic](limbic/README.md) | ☁️ your cloud | Configurable advisory emotional state service |
| [memory-core-mcp](memory-core-mcp/README.md) | 🏠 your computer | Separate local memory implementation |
| [mind-backend](mind-backend/README.md) | ☁️ your cloud | Memory, continuity, and retrieval service |
| [qualia-mcp](qualia-mcp/README.md) | 🏠 your computer | Local stdio adapter for current cloud Qualia |

**Tip:** Most people start with **Qualia** (`mind-backend`) for memory, then **Limbic** for feelings. Want everything to stay on your own computer? `memory-core-mcp` is a fully local memory instead.

🏠 = runs on your computer. ☁️ = a small helper in your own free Cloudflare account; it is never "Anam in the cloud".

[← Back to all toolboxes](../README.md)
