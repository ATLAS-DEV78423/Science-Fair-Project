# Blender ↔ OpenCode (MCP) — oncolytic virotherapy science-fair project

Status: **working, verified end-to-end** on 2026-09-27.

## Layout

```
science-fair-virotherapy/
├── blender/
│   ├── scenes/     # .blend files
│   ├── scripts/    # Python build scripts
│   ├── assets/     # imported models, textures, HDRIs
│   └── renders/    # output images
├── web/            # any web front-end for the visualization
├── docs/           # notes, references, write-up material
└── README.md
```

The Blender scene scaffold is started — see [`blender/README.md`](blender/README.md)
for the collection structure, naming convention and rebuild workflow. **No
biological assets exist yet**: `virotherapy_main.blend` currently holds
collections, controllers, cameras, lights and render settings only, and the
asset/animation scripts are API contracts that raise `NotImplementedError`.

## How the connection works

Three pieces, all local:

```
OpenCode  ⇐  MCP over stdio  ⇒  blender-mcp  ⇐  TCP 127.0.0.1:9876  ⇒  Blender add-on
```

| Piece | What it is | Where it lives |
|---|---|---|
| Blender **5.2.1 LTS** | the app | `/usr/bin/blender` (Arch `extra`) |
| **MCP add-on v1.0.3** | runs *inside* Blender, executes requests on its main thread | `~/.config/blender/5.2/extensions/lab_blender_org/mcp` |
| **`blender-mcp` server** | bridges stdio ↔ TCP; what OpenCode launches | `~/blender_mcp/mcp` (official git clone) |

The add-on is Blender Foundation's own — Blender Lab project, not a third party.
Source: <https://www.blender.org/lab/mcp-server/> · <https://projects.blender.org/lab/blender_mcp>

⚠️ There is an older, unrelated community add-on also called "Blender MCP"
(the `ahujasid` one). It speaks a different protocol. Everything here uses the
**official** one. The PyPI package `blender-mcp` is *also* the old community
project, renamed — so `uvx blender-mcp` is wrong. We run from source instead.

## How to start things

### 1. Start Blender (required — it hosts the add-on)

```sh
blender
```

Nothing else is needed. The add-on's **Start at launch** is already enabled, so
~1 second after the window opens it listens on `127.0.0.1:9876`.

**Blender must stay open** for OpenCode to control it. If Blender is closed, MCP
tool calls fail with a connection error; OpenCode itself still starts fine.

### 2. OpenCode

No startup step — the MCP server is launched on demand, per session, by OpenCode.
Just start `opencode` and ask it to use the blender tools.

If Blender's server didn't autostart, click **Start MCP Bridge Server** in
`Edit → Preferences → Add-ons → MCP`. Or from a terminal:

```sh
blender --background --python-expr "import bpy; bpy.ops.blmcp.server_start()"
```

### 3. How OpenCode accesses Blender

26 tools are exposed, named `blender_*`. The ones that matter:

| Tool | Use |
|---|---|
| `blender_execute_blender_code` | run arbitrary Python in Blender — the main tool |
| `blender_get_objects_summary` | list the scene / collection hierarchy |
| `blender_get_object_detail_summary` | details for one named object |
| `blender_get_screenshot_of_window_as_image` | screenshot the live Blender window |
| `blender_render_viewport_to_path` | render the viewport to a file |
| `blender_jump_to_view3d_object_by_name` | frame an object in the viewport |
| `blender_get_python_api_docs` | look up `bpy` API for the *running* version |

Example prompt:

> Use the blender MCP tools to add a subdivided Icosphere at the origin named
> `Virion`, then screenshot the window.

## Testing the connection

```sh
# 1. is OpenCode seeing the server?
opencode mcp list          # expect: ● ✓ blender connected

# 2. is Blender's add-on listening? (only while Blender is open)
ss -tlnp | grep 9876       # expect: LISTEN 127.0.0.1:9876  users:(("blender",...))

# 3. end-to-end, drives real Blender and prints what it did
opencode run "Use the blender MCP tools: list the objects in the current scene \
and tell me their names and locations."
```

The verified smoke test lives at
`blender/scenes/mcp-smoke-test.blend` (cube + UV sphere + one material), with a
screenshot of it in `blender/renders/mcp-e2e-proof.png`.

## Shutting down

- **Blender**: close the window. The add-on's socket dies with it; nothing else to clean up.
- **MCP server**: it is a child of OpenCode, so it exits when OpenCode exits. To check nothing is orphaned: `pgrep -af blender-mcp`
- **Full reset** (removes the OpenCode entry; add-on and clone stay):
  restore `~/.config/opencode/opencode.json.bak-before-blender-mcp` over `opencode.json`.

## Configuration reference

`~/.config/opencode/opencode.json` — one added entry, nothing else touched:

```json
"blender": {
  "type": "local",
  "command": ["uv", "--directory", "/home/stanley/blender_mcp/mcp", "run", "blender-mcp"]
}
```

The `uv --directory ... run` form is the official documented invocation; it keeps
the server's dependencies (`mcp`, `pyyaml`, `docutils`) inside
`~/blender_mcp/mcp/.venv` instead of the system Python.

Blender add-on settings (`Edit → Preferences → Add-ons → MCP`): host `localhost`,
port `9876`, Start at launch **on**. Backed up in
`~/.config/blender/5.2/config/userpref.blend`.

## Security notes — read before putting real work in here

Blender's own documentation is blunt about this, and it is worth taking seriously:

- **`execute_blender_code` runs AI-generated Python inside Blender with no real
  sandbox.** The add-on has a `weak_sandbox.py`, but its own docstring calls it
  "a slap on the wrist" that mainly blocks `sys.exit()`. It will not stop code
  that deletes your scene or writes files elsewhere on disk.
- **Port 9876 is unauthenticated.** Any process running as any local user can
  drive your Blender. It is bound to `127.0.0.1` only — not reachable from the
  network — but there is no credential on it.
- **Blender's "Online Access" preference is enabled** (it is required for the
  add-on's socket server; without it the add-on refuses to start). That is a
  broader permission than this setup strictly needs, but it is Blender's gate.
- **Save before you experiment.** `Ctrl+S` in Blender. The MCP tools change the
  live scene, and a bad prompt can undo an hour of modelling.

Mitigations in place: everything is loopback-only, and project files are confined
to this directory. Blender's docs suggest a VM or a machine with no sensitive data
if that is not good enough for you.
