# borg-plugin — a Claude Code marketplace

This directory is a self-contained Claude Code **plugin marketplace** exposing a
single plugin: **`borg`**. It can be lifted out of the claude-os repo and published
as its own git repository unchanged.

```
borg-plugin/
├── .claude-plugin/
│   └── marketplace.json          # marketplace manifest → lists the borg plugin
└── plugins/
    └── borg/
        ├── .claude-plugin/plugin.json
        ├── commands/borg.md       # the /borg slash command
        ├── agents/borg-queen.md   # the Borg Queen subagent
        ├── scripts/borg-lib.sh    # curl primitives for the memory backend
        ├── scripts/borg-drone.sh  # external ollama-drone launcher
        └── README.md              # plugin docs + pluggable-memory contract
```

## Install

```text
/plugin marketplace add /path/to/claude-os/borg-plugin
/plugin install borg@borg
/borg <your plan>
```

The plugin defaults to the [Claude OS](https://github.com/brobertsaz/claude-os)
memory backend but is pluggable via an HTTP contract — see
[`plugins/borg/README.md`](plugins/borg/README.md).
