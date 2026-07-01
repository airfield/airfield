# Command tree

A flowchart of the full Airfield CLI command surface, generated from the source of
truth in `src/airfield/main.py` (and `src/airfield/cli/subprojects.py`).

Every invocation is `airfield <namespace> <command> [args]`, e.g.
`airfield project up navstack` = `airfield` → `project` → `up` → arg `navstack`.
The only two leaves that skip a namespace are `status` and `doctor`.

```
airfield
│
├─ package ……………… package operations
│    ├─ init                 create / wrap a package
│    ├─ deinit               remove airfield config from package
│    ├─ build                build a package image
│    ├─ shell                open a shell in the package container
│    ├─ cmd                  run an arbitrary command in the container (cmd <pkg> -- …)
│    ├─ run                  run a named command from the package's `run:` map
│    └─ dependencies ……… dependency-repo operations
│         ├─ check
│         └─ upstream
│
├─ project ……………… project operations
│    ├─ init                 scaffold a new project
│    ├─ deinit               remove airfield config from project
│    ├─ run                  run a package's `default` command (or shell)
│    ├─ liftoff              run a plan
│    └─ up                   generate tmuxinator file/session from a plan   ← e.g. `airfield project up navstack`
│
├─ tools ………………… system tools
│    └─ system ………… system maintenance
│         ├─ clean
│         ├─ setup
│         ├─ update
│         ├─ alias
│         └─ install-completion
│
├─ system ………………  ⟵ same group as `tools system`, exposed at top level
│    ├─ clean
│    ├─ setup
│    ├─ update
│    ├─ alias
│    └─ install-completion
│
├─ docker ………………  docker build optimization
│    └─ cache
│
├─ subpackages ………  multi-repo source-code ops (module is `subprojects.py`)
│    ├─ status
│    ├─ commit
│    ├─ push
│    ├─ pull
│    ├─ stash
│    ├─ clean
│    ├─ track
│    ├─ checkout
│    ├─ undo
│    └─ diff
│
├─ status …………………  (leaf) print project/package context & runtime status
└─ doctor …………………  (leaf) check system dependencies   [--fix]
```

## Prefix matching

The `PrefixGroup` class in `main.py` accepts any unambiguous prefix at each level,
so `airfield proj up navstack` or `airfield package dep check` work. A prefix that
matches two commands at the same level errors out as ambiguous.
