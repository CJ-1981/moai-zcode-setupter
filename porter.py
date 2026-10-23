"""Port moai-adk Claude Code templates into ZCode-compatible project files.

Steps performed on a target project:
1. Run `moai-adk init` (scaffolds .moai/ spec/config tree).
2. Copy CLAUDE.md template -> AGENTS.md.
3. Copy selected .claude/skills/* into .zcode/skills/ (frontmatter cleaned).
4. Convert .claude/commands/moai/*.md into .zcode/skills/ skills.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

MOAI_BIN = os.path.expanduser("~/.local/bin/moai-adk")


def find_templates_dir() -> str:
    pattern = os.path.expanduser(
        "~/.local/share/uv/tools/moai-adk/lib/python*/site-packages/moai_adk/templates"
    )
    matches = sorted(glob.glob(pattern))
    if not matches:
        raise FileNotFoundError(
            "moai-adk templates not found. Is moai-adk installed via `uv tool install moai-adk`?"
        )
    return matches[-1]


# Skill categories available in the templates (prefix -> label shown in UI).
SKILL_CATEGORIES = {
    "foundation": "moai-foundation-* (TRUST-5, SPEC-First TDD core) — required",
    "workflow": "moai-workflow-* (spec/testing/docs workflow) — required",
    "domain": "moai-domain-* (backend/frontend/database/uiux)",
    "lang": "moai-lang-* (programming languages)",
    "platform": "moai-platform-* (auth0, supabase, vercel, ...)",
    "library": "moai-library-* (mermaid, nextra, shadcn)",
    "mcp": "moai-mcp-* (figma, notion)",
}
REQUIRED_CATEGORIES = {"foundation", "workflow"}


@dataclass
class SetupReport:
    target: str
    steps: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    copied_skills: list = field(default_factory=list)
    converted_commands: list = field(default_factory=list)

    def log(self, msg: str) -> None:
        self.steps.append(msg)


def run_moai_init(target: str, report: SetupReport) -> None:
    if not os.path.exists(MOAI_BIN):
        report.warnings.append(
            f"moai-adk CLI not found at {MOAI_BIN}; skipping `moai-adk init`. "
            "Create .moai/ manually or install with `uv tool install moai-adk`."
        )
        return
    proc = subprocess.run(
        [MOAI_BIN, "init", "-y", "--force"],
        cwd=target,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = (proc.stdout + proc.stderr).strip()
    if output:
        report.log(f"moai-adk init:\n{output}")
    if proc.returncode != 0:
        report.warnings.append(f"`moai-adk init` exited with {proc.returncode}")
    else:
        report.log("`moai-adk init` completed")


_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, text[m.end():]


def clean_skill_md(text: str) -> str:
    """Strip Claude-only frontmatter keys (allowed-tools etc.) from a SKILL.md."""
    meta, body = _parse_frontmatter(text)
    if not meta:
        return text
    keep = {k: v for k, v in meta.items() if k not in ("allowed-tools", "model")}
    fm = "---\n" + "\n".join(f"{k}: {v}" for k, v in keep.items()) + "\n---\n"
    return fm + body


def copy_skills(templates: str, target: str, categories: set[str], report: SetupReport) -> None:
    src_root = os.path.join(templates, ".claude", "skills")
    dst_root = os.path.join(target, ".zcode", "skills")
    for name in sorted(os.listdir(src_root)):
        cat = name.removeprefix("moai-").split("-")[0]
        if cat not in SKILL_CATEGORIES:
            continue  # e.g. moai-worktree, moai-ai-nano-banana; skip uncategorized
        if cat not in categories and cat not in REQUIRED_CATEGORIES:
            continue
        src = os.path.join(src_root, name)
        dst = os.path.join(dst_root, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
            if name == "SKILL.md":
                continue
        skill_md = os.path.join(dst, "SKILL.md")
        if os.path.isfile(skill_md):
            with open(skill_md) as f:
                text = f.read()
            with open(skill_md, "w") as f:
                f.write(clean_skill_md(text))
        report.copied_skills.append(name)
    report.log(f"Copied {len(report.copied_skills)} skills to .zcode/skills/")


def convert_command(text: str, filename: str) -> str:
    """Convert a Claude Code command file into a ZCode SKILL.md body."""
    meta, body = _parse_frontmatter(text)
    slug = filename.removesuffix(".md")  # e.g. "1-plan"
    skill_name = "moai-" + slug

    # !command (or !`command`) pre-execution lines -> instructions to run them.
    body = re.sub(
        r"^!?`?(.+?)`?$",
        lambda m: (
            f"- Run in the project shell: `{m.group(1).strip()}`"
            if m.group(0).startswith("!")
            else m.group(0)
        ),
        body,
        flags=re.MULTILINE,
    )
    # @file includes -> instructions to read them.
    body = re.sub(
        r"^@(\S+)$",
        r"- Read this file first: `\1`",
        body,
        flags=re.MULTILINE,
    )
    body = body.replace("$ARGUMENTS", "<the user's arguments to this skill>")

    description = meta.get("description", f"MoAI-ADK {slug} step (ported from Claude Code)")
    header = (
        f"---\nname: {skill_name}\n"
        f'description: "MoAI-ADK workflow step (ported from Claude Code). {description}"\n'
        "---\n\n"
        "# Ported Claude Code command: moai:" + slug + "\n\n"
        "This skill was converted from a Claude Code slash command. Claude-specific "
        "mechanics (`!cmd` context, `@file` includes, `$ARGUMENTS`) were rewritten as "
        "instructions above; follow them literally before continuing.\n\n"
    )
    return header + body


def convert_commands(templates: str, target: str, report: SetupReport) -> None:
    src_root = os.path.join(templates, ".claude", "commands", "moai")
    dst_root = os.path.join(target, ".zcode", "skills")
    for filename in sorted(os.listdir(src_root)):
        if not filename.endswith(".md"):
            continue
        with open(os.path.join(src_root, filename)) as f:
            text = f.read()
        slug = filename.removesuffix(".md")
        skill_dir = os.path.join(dst_root, f"moai-cmd-{slug}")
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write(convert_command(text, filename))
        report.converted_commands.append(f"moai-cmd-{slug}")
    report.log(
        f"Converted {len(report.converted_commands)} commands to skills: "
        + ", ".join(report.converted_commands)
    )


def write_agents_md(templates: str, target: str, report: SetupReport) -> None:
    src = os.path.join(templates, "CLAUDE.md")
    dst = os.path.join(target, "AGENTS.md")
    if os.path.exists(dst):
        report.warnings.append("AGENTS.md already exists; left untouched.")
        return
    if not os.path.isfile(src):
        report.warnings.append("CLAUDE.md template not found; AGENTS.md not created.")
        return
    with open(src) as f:
        text = f.read()
    note = (
        "<!-- Ported from moai-adk CLAUDE.md template for ZCode. References to "
        "`/moai:*` slash commands map to the `moai-cmd-*` skills in .zcode/skills/. -->\n\n"
    )
    with open(dst, "w") as f:
        f.write(note + text)
    report.log("Created AGENTS.md (from CLAUDE.md template)")


ZCODE_GITIGNORE_BLOCK = """\
# ---------------------------------------------
# ZCode (ported MoAI-ADK skills are tracked; local state ignored)
# ---------------------------------------------
.zcode/settings.local*
.zcode/*.local.*

# ZCode skills secrets (mirror moai-adk's .claude/skills rules)
.zcode/skills/*/.env*
.zcode/skills/*/secrets/
.zcode/skills/*/*/*secret*
.zcode/skills/*/*/*key*
.zcode/skills/*/*/*token*
"""


def update_gitignore(target: str, report: SetupReport) -> None:
    """Append the ZCode section to the project .gitignore if not present.

    .zcode/skills/ and AGENTS.md are intentionally tracked: they are project
    workflow assets (like moai-adk's .claude/commands), shared with the team.
    """
    path = os.path.join(target, ".gitignore")
    marker = "# ZCode (ported MoAI-ADK skills"
    existing = ""
    if os.path.isfile(path):
        with open(path) as f:
            existing = f.read()
        if marker in existing:
            report.log(".gitignore already has a ZCode section; left untouched.")
            return
    with open(path, "a") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write("\n" + ZCODE_GITIGNORE_BLOCK)
    report.log("Appended ZCode section to .gitignore (skills tracked, local state/secrets ignored)")


def setup_project(target: str, categories: set[str]) -> SetupReport:
    report = SetupReport(target=target)
    if not os.path.isdir(target):
        raise NotADirectoryError(target)
    if not os.path.exists(os.path.join(target, ".git")):
        report.warnings.append("Target is not a git repository root (no .git found).")

    report.log("Running moai-adk init ...")
    run_moai_init(target, report)

    templates = find_templates_dir()
    report.log(f"Using templates from {templates}")

    write_agents_md(templates, target, report)
    copy_skills(templates, target, categories, report)
    convert_commands(templates, target, report)
    update_gitignore(target, report)
    return report


# ---------------------------------------------------------------------------
# Clean / uninstall
# ---------------------------------------------------------------------------

# Cleanable items: key -> (path relative to project, risk level)
# "safe" items were created by this setupter for ZCode; "claude" items belong
# to moai-adk's Claude Code integration and are opt-in only.
CLEAN_ITEMS = {
    "skills": ("ZCode skills (.zcode/skills/moai-*)", "safe"),
    "agents_md": ("AGENTS.md (ZCode instructions)", "safe"),
    "gitignore": ("ZCode section in .gitignore", "safe"),
    "claude_dir": (".claude/ (Claude Code commands/hooks/skills)", "claude"),
    "claude_md": ("CLAUDE.md (Claude Code instructions)", "claude"),
    "moai_dir": (".moai/ (specs, config, memory - your SPEC content!)", "claude"),
    "moai_backups": (".moai-backups/ (init backups)", "claude"),
}


def _skill_is_moai(name: str) -> bool:
    return name.startswith("moai-")


def clean_project(target: str, items: set[str]) -> SetupReport:
    """Remove selected moai-adk artifacts from a project.

    Only moai-prefixed skills are deleted from .zcode/skills/ — any other
    skills you keep there are untouched. The .zcode directory itself is
    removed if it ends up empty.
    """
    report = SetupReport(target=target)
    if not os.path.isdir(target):
        raise NotADirectoryError(target)
    unknown = items - set(CLEAN_ITEMS)
    if unknown:
        raise ValueError(f"Unknown clean items: {sorted(unknown)}")

    if "skills" in items:
        skills_dir = os.path.join(target, ".zcode", "skills")
        removed = 0
        if os.path.isdir(skills_dir):
            for name in sorted(os.listdir(skills_dir)):
                if _skill_is_moai(name):
                    path = os.path.join(skills_dir, name)
                    shutil.rmtree(path, ignore_errors=True)
                    removed += 1
                    report.copied_skills.append(name)
            if not os.listdir(skills_dir):
                os.rmdir(skills_dir)
                zcode = os.path.join(target, ".zcode")
                if os.path.isdir(zcode) and not os.listdir(zcode):
                    os.rmdir(zcode)
        report.log(f"Removed {removed} moai-* skills from .zcode/skills/")

    if "agents_md" in items:
        path = os.path.join(target, "AGENTS.md")
        if os.path.isfile(path):
            with open(path) as f:
                head = f.read(400)
            if "moai-adk" in head:
                os.remove(path)
                report.log("Removed AGENTS.md (created by this setupter)")
            else:
                report.warnings.append(
                    "AGENTS.md exists but has no moai-adk marker; left untouched."
                )
        else:
            report.log("AGENTS.md not present; nothing to do.")

    if "gitignore" in items:
        path = os.path.join(target, ".gitignore")
        if os.path.isfile(path):
            with open(path) as f:
                text = f.read()
            if ZCODE_GITIGNORE_BLOCK.strip() in text:
                text = text.replace("\n" + ZCODE_GITIGNORE_BLOCK, "").replace(
                    ZCODE_GITIGNORE_BLOCK, ""
                )
                with open(path, "w") as f:
                    f.write(text)
                report.log("Removed ZCode section from .gitignore")
            else:
                report.log(".gitignore has no matching ZCode section; left untouched.")

    for key in ("claude_dir", "claude_md", "moai_dir", "moai_backups"):
        if key not in items:
            continue
        rel = CLEAN_ITEMS[key][0].split(" ")[0]  # e.g. ".claude/"
        path = os.path.join(target, rel.rstrip("/"))
        if os.path.isdir(path):
            shutil.rmtree(path)
            report.log(f"Removed {rel}")
        elif os.path.isfile(path):
            os.remove(path)
            report.log(f"Removed {rel}")
        else:
            report.log(f"{rel} not present; nothing to do.")

    report.log("Clean complete.")
    return report
