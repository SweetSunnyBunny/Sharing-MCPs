# Krita plugin — only needed for Krita drawing tools

Skip this folder if you only want filesystem, terminal or clipboard tools.
For Krita, this plugin is the part that runs **inside the Krita application**;
the [Machine Agent](../../../README.md) bridge is a separate process.

## 1. Copy the two plugin items

1. Install and open Krita on Windows.
2. Use Krita's **Settings → Manage Resources → Open Resource Folder** to find
   its actual resource folder. Note that path, then close Krita.
3. In that resource folder, open or create a folder named `pykrita`.
4. From this package's `tools/krita/plugin` folder, copy both items into
   `pykrita`: the complete **`krita_mcp_plugin` folder** and the separate
   **`krita_mcp_plugin.desktop` file**. Keep the names unchanged. Do not copy
   only `__init__.py`, and do not put an extra `plugin` folder around them.

The resulting layout must look like:

```text
YOUR-KRITA-RESOURCE-FOLDER/
  pykrita/
    krita_mcp_plugin.desktop
    krita_mcp_plugin/
      __init__.py
```

## 2. Enable and verify it

Start Krita. In **Settings → Configure Krita → Python Plugin Manager**, enable
**Krita MCP Plugin**. Restart Krita once after enabling it, then keep Krita open.
Open PowerShell and run:

```powershell
Invoke-RestMethod http://127.0.0.1:5678/health
```

A JSON response with `status: ok` confirms the plugin is running. It uses port
5678. The shipped plugin listens on network interfaces without authentication,
so do not forward that port or permit public/network access to it; the bridge
on this PC is the intended caller.

## 3. Connect the machine bridge

Complete the parent [Machine Agent guide](../../../README.md), keep its bridge
running and choose the `krita` MCP proxy group. Create/open a test document in
Krita before trying document operations. Start with a read-only document/tool
inspection; then make a small change in that test document.

## Common fixes and implementation notes

- **Plugin absent in the manager:** check the exact two-item layout and that
  you used Krita's own resource directory, not a similarly named folder.
- **Health connection refused:** restart Krita, confirm the plugin is enabled,
  and check that another program is not already using port 5678.
- **Bridge works but Krita calls fail:** both Krita and the plugin must remain
  open; the bridge cannot draw in a closed application.
- **Save/export stalls:** use a new test file and inspect the returned error.
  This patched plugin marshals commands onto Krita's main Qt thread and reports
  save/export failures. `krita_export` is generally the simpler unattended
  output path; native `.kra` saves also depend on Krita's document save pipeline.

Installing Python separately for Machine Agent does not install this plugin
inside Krita. Both pieces are needed for the complete drawing route.
