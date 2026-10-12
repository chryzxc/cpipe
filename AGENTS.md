# Working on cpipe

cpipe is a general-purpose Hermes plugin. Other people run it with their own profiles, tools, models and repositories.

- **No tool lock-in.** Never name, require or special-case a specific user tool (a code index, MCP server, skill, CLI or model) in code, prompts or docs. Describe the capability instead ("a code index or code-navigation tool, if you have one") and let each user's setup decide.
- **Respect the user's Hermes setup.** cpipe never changes Hermes profiles, config, toolsets, skills or plugins, and never modifies Hermes itself.
- **Repository-agnostic.** No repository-, client- or person-specific names, paths or rules. Tests use fixtures only.
- **Neutral docs.** README.md describes the product as it is.
