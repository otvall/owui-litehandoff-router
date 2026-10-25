# Inline Visualizer: browser evidence, 2026-10-23

Safari 27.0.1, local OWUI 0.12.3, ordinary synthetic viewer. CUA captured the
actions and screenshots; these AX observations contain no credentials/session
IDs. Backend evidence stays separate in [visualizer-evidence.json](visualizer-evidence.json).

| Execution | Observed browser result |
| --- | --- |
| `674713a6-9468-4fec-a5a9-687cd663b049` | Opened from Sources → A → Открыть выполнение. Embedded Content about:srcdoc; heading/image/Toggle VGFIX-123113be9500-1; STATE=0→1. Screenshot shows green circle and Local SVG 42; loader absent |
| Same saved child | Reload restored SVG/Toggle; click STATE=0→1 again. A new ordinary Safari window/socket also restored and interacted with the child after the original tab disconnected. Model/Tool counters unchanged |
| A+B children `5027ea3b-…`, `c1b85915-…` | Separate VGFIX-f7e5f607ca91-1/2 headings and SVGs; each Toggle produces STATE=1 |
| A+A children `471b8059-…`, `96a02325-…` | Separate VGFIX-2ec34627c906-1/2; each SVG/Toggle produces STATE=1; no overwrite |
| Live child `7c61a4ea-14e4-4c41-9edb-c237211c43a7` | Opened via source at done=false after Tool; Rendering visualization observed. After release, loader disappears and VGFIX-3495d965d5f4-1 SVG/Toggle appears; STATE=0→1 |
| Final A `dc32a0b6-5a06-4669-b24d-c32286618978` | Source link; VGFIX-c4cc1d7fd3d2-1 heading/image/Toggle, STATE=0→1, loader absent |
| Final B `6f5f1aec-2b56-42e8-987e-f19dd5131d9d` | Source link; VGFIX-c4cc1d7fd3d2-2 heading/image/Toggle, STATE=0→1, loader absent |

Network was cleared before the final A+B request and retained through both
source-link navigations and interactions. The complete AX table contained
**47 resources: 45 localhost entries and two about:srcdoc documents**.
Web Inspector counts the empty-domain srcdocs as its second domain; these are
embedded documents, not external HTTP requests.

All HTTP rows belonged to **127.0.0.1**, with these names (including repeated API
reads and periodic version checks):

```text
version.json                         fetch 304
completions                          fetch 200
chats, pinned, folders, shared, tags  fetch 200
8cbd36b2-6cf3-401c-9d52-ce9ebefa3007 fetch 200
dc32a0b6-5a06-4669-b24d-c32286618978 fetch 200
6f5f1aec-2b56-42e8-987e-f19dd5131d9d fetch 200
favicon.png, apple-touch-icon.png,
favicon-96x96.png                    png   200
notification.mp3 (two byte ranges)   mp3   206
about:srcdoc                        document 161.49 KB 141 ms
about:srcdoc                        document 161.49 KB 174 ms
```

No CDN, external image/font, fetch or other external host occurred in this
control. Local browser/backend transport stayed enabled. The provider used
container loopback 8769 and preinstalled dependencies; Tool/Skill were local
hash-verified files. External libraries and other browser/plugin versions were
not checked.

Direct-main handoff also executed; its saved visualization worked after a
toolbar reload, while its live loader had remained visible before reload.
The ordinary A+B→C/A+A→C controls passed. Live-main rendering is outside the
child-execution guarantee proven here.

The viewer signed out at teardown (`/auth?state=logout`). A fresh native process
audit without a browser session reported zero tickets and viewer sockets before
fixture deletion.
