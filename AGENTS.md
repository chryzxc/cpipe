# Working on cpipe

cpipe is a general-purpose Hermes plugin made of skills. Other people run it with their own profiles, tools,
models and repositories.

- **Skills drive the flow.** The model runs every step from the skills in `skills/`. `__init__.py` only registers
  them and the `/cpipe` command. Do not add code that decides the next step, calls a model, or stops a run;
  change the skill text instead.
- **Keep going.** A skill change must never add a new reason to stop or a message that asks the user to fix or
  run something. A run never waits for the user (Unattended in `skills/run/SKILL.md`).
- **No tool lock-in.** Never name, require or special-case a specific user tool (a code index, MCP server, skill,
  CLI or model). Describe the capability instead and let each user's setup decide.
- **Respect the user's Hermes setup.** cpipe never changes Hermes profiles, config, toolsets, skills or plugins,
  and never modifies Hermes itself.
- **Repository-agnostic.** No repository-, client- or person-specific names, paths or rules.
- **Skill format.** Description of at most 60 characters ending in a period; each skill short; long material in a
  file next to it, loaded with `skill_view(name=..., file_path=...)`.
- **Neutral docs.** README.md describes the product as it is.
