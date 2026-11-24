# Trove skills (progressive disclosure)

Each folder is one skill. The assistant never sees all of this at once:

| Level | What is loaded | When |
|---|---|---|
| 1 · Catalog | `name` + `description` from each SKILL.md front matter (~1 line) | Always, in the system prompt |
| 2 · Instructions | The SKILL.md body | Only when the model calls `load_skill`, or the local router picks it |
| 3 · Resources | Files in `reference/` | Only when the model calls `read_skill_resource` |
| Tools | Read-only case tools listed in `tools:` | Exposed to the model only after their skill is loaded |

Front matter keys: `name`, `description`, `triggers` (comma-separated words for
the offline router), `tools` (comma-separated tool names from `assistant.py`).
A `## Offline summary` section is returned when the LLM is not used.

Add a skill by creating a folder with a SKILL.md. No code change is needed
unless it declares a new tool.
