# User ops

The authoring guide and primitives reference for user-written ops ship with the plugin, so an
installed plugin carries them: [`plugin/skills/write-new-op/USER-OPS.md`](../plugin/skills/write-new-op/USER-OPS.md).
The procedure an agent follows is [`plugin/skills/write-new-op/SKILL.md`](../plugin/skills/write-new-op/SKILL.md),
and [`plugin/skills/write-new-op/examples/counter-op/`](../plugin/skills/write-new-op/examples/counter-op/)
is a complete, tested example op.

A user op runs with the same trust as any Python you run; the engine's checks are correctness
gates, not a sandbox. Read an op before you allow it (`--allow-user-ops`).
