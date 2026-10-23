# MoAI-ADK → ZCode Setupter

A small PySide6 (Qt) desktop app that ports the
[MoAI-ADK](https://pypi.org/project/moai-adk/) SPEC-First TDD workflow
(EARS requirements, TRUST-5 quality gates, plan → run → sync) into any git
project so it can be used from **ZCode**, not just Claude Code.

![Main window](docs/screenshot-main.png)

## Why

MoAI-ADK integrates with Claude Code via `.claude/` commands, skills, hooks
and a `CLAUDE.md`. ZCode reads none of those — it uses `.zcode/skills/` and
`AGENTS.md`. This app bridges the two: pick a project folder, click once, and
the whole workflow becomes available in ZCode while Claude Code keeps working
unchanged in the same repo.

## What it does on the chosen project folder

1. Runs `moai-adk init -y --force` — scaffolds the `.moai/` spec/config tree
   (safe on already-initialized projects: user content is preserved and a
   backup is written to `.moai-backups/`).
2. Creates `AGENTS.md` from MoAI-ADK's `CLAUDE.md` template (never overwrites
   an existing one).
3. Copies the selected `moai-*` skills into `.zcode/skills/`. Foundation and
   workflow categories (TRUST-5, SPEC-First TDD, spec/testing workflow) are
   always included; domain / lang / platform / library / mcp skills are
   optional checkboxes.
4. Converts the 5 Claude Code slash commands (`0-project`, `1-plan`, `2-run`,
   `3-sync`, `9-feedback`) into ZCode skills:
   - `!cmd` pre-execution lines → "Run in the project shell" instructions
   - `@file` includes → "Read this file first" instructions
   - `$ARGUMENTS` → a described placeholder
5. Appends a small ZCode section to `.gitignore`: ported skills and
   `AGENTS.md` are tracked (they are project workflow assets), while local
   state and secret patterns under `.zcode/skills/` are ignored.

### Clean / uninstall

![Clean dialog](docs/screenshot-clean.png)

The **Clean** button removes exactly what the setupter added — only
`moai-*`-prefixed skills are deleted from `.zcode/skills/`, so your own
skills are never touched. Removing the Claude Code side (`.claude/`,
`CLAUDE.md`, `.moai/`) is opt-in via checkboxes; `.moai/` holds your SPEC
documents, so think twice before checking it.

## Requirements

- macOS (tested on Apple Silicon); should work on Linux too
- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- MoAI-ADK installed as a CLI tool:

  ```bash
  uv tool install moai-adk
  ```

## Run from source

```bash
git clone https://github.com/CJ-1981/moai-zcode-setupter.git
cd moai-zcode-setupter
uv run python main.py
```

## Usage

1. Launch the app, choose the target git repo folder.
2. Tick the optional skill categories you want (language / domain / platform
   skills for your stack).
3. Click **Run setup**.
4. Open the project in ZCode and run the workflow:

   ```
   /moai-cmd-0-project     # one-time project setup / config merge
   /moai-cmd-1-plan "..."  # write an EARS spec
   /moai-cmd-2-run         # TDD implementation (red → green → refactor)
   /moai-cmd-3-sync        # sync specs, docs and branch
   ```

Claude Code continues to work in the same repository with its original
`.claude/` and `CLAUDE.md` files — both clients share the `.moai/` spec tree.

## Multi-tool notes

- Both tools read/write the same `.moai/specs/`; avoid running the workflow
  in both tools concurrently on the same worktree.
- After a `moai-adk update`, re-run the setupter to refresh the ported skills
  (they overwrite cleanly). `AGENTS.md` is never overwritten — delete it
  first if you want the new template.
- MoAI-ADK's Claude Code hooks do not fire in ZCode; in ZCode the TRUST-5
  gates are enforced by the skill instructions and `AGENTS.md`.

## License

MIT
