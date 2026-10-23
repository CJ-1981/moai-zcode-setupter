"""Port moai-adk Claude Code assets into ZCode-compatible project files.

Supports two moai-adk generations:

- v3.x (Go binary, "moai" / "moai-adk" on PATH): runs
  `moai init --non-interactive --force`, then ports the project-deployed
  `.claude/skills` + `.claude/commands/moai`. If no v3 binary is installed,
  falls back to a shallow GitHub clone of modu-ai/moai-adk (read-only port,
  no init).
- v0.x / v1.x (legacy Python package via `uv tool install moai-adk`): runs
  `moai-adk init -y --force` and ports templates from the uv site-packages
  path.

What gets written for ZCode:
- `.zcode/skills/<skill>/` — moai skills (frontmatter cleaned)
- `.zcode/skills/moai-cmd-<name>/SKILL.md` — the slash commands converted
  into ZCode skills
- `AGENTS.md` (v3 init creates it natively; legacy path creates it from the
  CLAUDE.md template; never overwritten)
- a small ZCode section in `.gitignore`
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

LEGACY_MOAI_BIN = os.path.expanduser("~/.local/bin/moai-adk")
CLONE_CACHE = os.path.expanduser("~/.moai-zcode-setupter/moai-adk-src")
MOAI_REPO = "https://github.com/modu-ai/moai-adk.git"


@dataclass
class SetupReport:
    target: str
    steps: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    copied_skills: list = field(default_factory=list)
    converted_commands: list = field(default_factory=list)

    def log(self, msg: str) -> None:
        self.steps.append(msg)


# ---------------------------------------------------------------------------
# Locate moai-adk: v3 binary > legacy python tool
# ---------------------------------------------------------------------------


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=kw.pop("timeout", 180), **kw)


def find_moai() -> tuple[str | None, str]:
    """Return (kind, version): kind in {"v3", "legacy", None}."""
    for name in ("moai", "moai-adk"):
        path = shutil.which(name) or (
            LEGACY_MOAI_BIN if name == "moai-adk" and os.path.exists(LEGACY_MOAI_BIN) else None
        )
        if not path:
            continue
        try:
            proc = _run([path, "--version"])
        except Exception:
            continue
        m = re.search(r"(\d+)\.(\d+)", proc.stdout + proc.stderr)
        if not m:
            continue
        major = int(m.group(1))
        version = m.group(0)
        if major >= 2:
            return "v3", version
        if name == "moai-adk":
            return "legacy", version
    return None, ""


def ensure_clone(report: SetupReport) -> str | None:
    """Shallow-clone (or refresh) the moai-adk repo as a template source."""
    marker = os.path.join(CLONE_CACHE, ".git")
    if os.path.isdir(marker):
        report.log(f"Refreshing cached clone at {CLONE_CACHE} ...")
        proc = _run(["git", "-C", CLONE_CACHE, "fetch", "--depth", "1", "origin", "main"])
        if proc.returncode == 0:
            _run(["git", "-C", CLONE_CACHE, "reset", "-q", "--hard", "FETCH_HEAD"])
            return CLONE_CACHE
        report.warnings.append("Could not refresh clone; using existing copy.")
        return CLONE_CACHE
    os.makedirs(os.path.dirname(CLONE_CACHE), exist_ok=True)
    report.log(f"Cloning {MOAI_REPO} (shallow) ...")
    proc = _run(["git", "clone", "--depth", "1", MOAI_REPO, CLONE_CACHE], timeout=300)
    if proc.returncode != 0:
        report.warnings.append(
            f"git clone failed: {(proc.stderr or proc.stdout).strip()[:200]}"
        )
        return None
    return CLONE_CACHE


# ---------------------------------------------------------------------------
# Frontmatter / body conversion (shared by both generations)
# ---------------------------------------------------------------------------

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
    """Strip Claude-only frontmatter keys and rewrite Claude-only mechanics."""
    meta, body = _parse_frontmatter(text)
    body = convert_claude_mechanics(body)
    if not meta:
        return body
    keep = {k: v for k, v in meta.items() if k not in ("allowed-tools", "model", "argument-hint")}
    fm = "---\n" + "\n".join(f"{k}: {v}" for k, v in keep.items()) + "\n---\n"
    return fm + body


def convert_claude_mechanics(body: str) -> str:
    # !command (or !`command`) pre-execution lines -> run instructions.
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
    # @file includes -> read instructions (only bare @path lines).
    body = re.sub(r"^@(\S+)$", r"- Read this file first: `\1`", body, flags=re.MULTILINE)
    body = body.replace("$ARGUMENTS", "<the user's arguments to this skill>")
    return body


def convert_command(text: str, filename: str) -> str:
    """Convert a Claude Code command file into a ZCode SKILL.md."""
    meta, body = _parse_frontmatter(text)
    slug = filename.removesuffix(".md")
    skill_name = "moai-cmd-" + slug
    body = convert_claude_mechanics(body)
    description = meta.get("description", f"MoAI-ADK {slug} step (ported from Claude Code)")
    header = (
        f"---\nname: {skill_name}\n"
        f'description: "MoAI workflow step (ported from Claude Code). {description}"\n'
        "---\n\n"
        f"# Ported Claude Code command: /moai {slug}\n\n"
        "Converted from a Claude Code slash command; `!cmd` context, `@file` "
        "includes and `$ARGUMENTS` were rewritten as instructions. Follow them "
        "literally before continuing.\n\n"
    )
    return header + body


# ---------------------------------------------------------------------------
# Skill categories
# ---------------------------------------------------------------------------

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


def _skill_category(name: str) -> str | None:
    if not name.startswith("moai-"):
        return None
    rest = name.removeprefix("moai-")
    for cat in SKILL_CATEGORIES:
        if rest.startswith(cat + "-"):
            return cat
    return None


def copy_skills_from(src_root: str, target: str, categories: set[str], report: SetupReport) -> None:
    """Copy moai skills from a .claude/skills (or templates) root."""
    dst_root = os.path.join(target, ".zcode", "skills")
    if not os.path.isdir(src_root):
        report.warnings.append(f"Skills source not found: {src_root}")
        return
    for name in sorted(os.listdir(src_root)):
        cat = _skill_category(name)
        if cat is None:  # uncategorized (e.g. the "moai" dispatcher is handled too)
            cat = "workflow" if name == "moai" else None
        if cat is None:
            continue
        if cat not in categories and cat not in REQUIRED_CATEGORIES:
            continue
        src = os.path.join(src_root, name)
        dst = os.path.join(dst_root, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
            skill_md = os.path.join(dst, "SKILL.md")
            if os.path.isfile(skill_md):
                with open(skill_md) as f:
                    text = f.read()
                with open(skill_md, "w") as f:
                    f.write(clean_skill_md(text))
            report.copied_skills.append(name)
    report.log(f"Copied {len(report.copied_skills)} skills to .zcode/skills/")


def convert_commands_from(src_root: str, target: str, report: SetupReport) -> None:
    dst_root = os.path.join(target, ".zcode", "skills")
    if not os.path.isdir(src_root):
        report.warnings.append(f"Commands source not found: {src_root}")
        return
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


# ---------------------------------------------------------------------------
# gitignore
# ---------------------------------------------------------------------------

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
    path = os.path.join(target, ".gitignore")
    existing = ""
    if os.path.isfile(path):
        with open(path) as f:
            existing = f.read()
        if "# ZCode (ported MoAI-ADK skills" in existing:
            report.log(".gitignore already has a ZCode section; left untouched.")
            return
    with open(path, "a") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write("\n" + ZCODE_GITIGNORE_BLOCK)
    report.log("Appended ZCode section to .gitignore (skills tracked, local state/secrets ignored)")


# ---------------------------------------------------------------------------
# Setup orchestration
# ---------------------------------------------------------------------------


def _find_legacy_templates() -> str:
    pattern = os.path.expanduser(
        "~/.local/share/uv/tools/moai-adk/lib/python*/site-packages/moai_adk/templates"
    )
    matches = sorted(glob.glob(pattern))
    if not matches:
        raise FileNotFoundError(
            "moai-adk v3 binary not found and legacy templates not found. "
            "Install moai-adk (v3: curl -fsSL https://adk.mo.ai.kr/install.sh | bash) "
            "or `uv tool install moai-adk` for the legacy version."
        )
    return matches[-1]


def setup_project(target: str, categories: set[str]) -> SetupReport:
    report = SetupReport(target=target)
    if not os.path.isdir(target):
        raise NotADirectoryError(target)
    if not os.path.exists(os.path.join(target, ".git")):
        report.warnings.append("Target is not a git repository root (no .git found).")

    kind, version = find_moai()

    if kind == "v3":
        report.log(f"Found moai-adk v3 binary ({version}); running moai init ...")
        binpath = shutil.which("moai") or shutil.which("moai-adk")
        proc = _run([binpath, "init", "--non-interactive", "--force"], cwd=target)
        out = (proc.stdout + proc.stderr).strip()
        if out:
            report.log(f"moai init:\n{out[-1500:]}")
        if proc.returncode != 0:
            report.warnings.append(f"`moai init` exited with {proc.returncode}")
        src_skills = os.path.join(target, ".claude", "skills")
        src_cmds = os.path.join(target, ".claude", "commands", "moai")
        # Also include user-folder skills deployed by init (~/.claude/skills),
        # since common moai skills live there in v3.
        user_skills = os.path.expanduser("~/.claude/skills")
        if os.path.isdir(src_skills):
            copy_skills_from(src_skills, target, categories, report)
        else:
            report.warnings.append("No .claude/skills in project after init.")
        if os.path.isdir(user_skills) and not report.copied_skills:
            report.log("Project had no skills; falling back to ~/.claude/skills")
            copy_skills_from(user_skills, target, categories, report)
        convert_commands_from(src_cmds, target, report)
        if os.path.isfile(os.path.join(target, "AGENTS.md")):
            report.log("AGENTS.md already created by moai init; left as is.")
        elif os.path.isfile(os.path.join(target, "CLAUDE.md")):
            # v3 only writes AGENTS.md for the gpt/both harness; derive it for
            # ZCode from the project's CLAUDE.md.
            with open(os.path.join(target, "CLAUDE.md")) as f:
                text = f.read()
            note = (
                "<!-- Derived from CLAUDE.md by MoAI-ZCode-Setupter for ZCode. "
                "`/moai <cmd>` slash commands map to the `moai-cmd-*` skills in "
                ".zcode/skills/. -->\n\n"
            )
            with open(os.path.join(target, "AGENTS.md"), "w") as f:
                f.write(note + text)
            report.log("Created AGENTS.md (derived from project CLAUDE.md)")
    elif kind == "legacy":
        report.log(f"Found legacy moai-adk ({version}); running init ...")
        proc = _run([LEGACY_MOAI_BIN, "init", "-y", "--force"], cwd=target)
        out = (proc.stdout + proc.stderr).strip()
        if out:
            report.log(f"moai-adk init:\n{out[-1500:]}")
        if proc.returncode != 0:
            report.warnings.append(f"`moai-adk init` exited with {proc.returncode}")
        templates = _find_legacy_templates()
        report.log(f"Using legacy templates from {templates}")
        # AGENTS.md from CLAUDE.md template
        src = os.path.join(templates, "CLAUDE.md")
        dst = os.path.join(target, "AGENTS.md")
        if os.path.exists(dst):
            report.warnings.append("AGENTS.md already exists; left untouched.")
        elif os.path.isfile(src):
            with open(src) as f:
                text = f.read()
            note = (
                "<!-- Ported from moai-adk CLAUDE.md template for ZCode. References to "
                "`/moai:*` slash commands map to the `moai-cmd-*` skills in .zcode/skills/. -->\n\n"
            )
            with open(dst, "w") as f:
                f.write(note + text)
            report.log("Created AGENTS.md (from CLAUDE.md template)")
        copy_skills_from(os.path.join(templates, ".claude", "skills"), target, categories, report)
        convert_commands_from(
            os.path.join(templates, ".claude", "commands", "moai"), target, report
        )
    else:
        # No moai-adk installed: read-only port from a GitHub clone.
        report.log("No moai-adk installation found; porting from GitHub clone (no init).")
        report.warnings.append(
            "moai-adk not installed: only skills/commands were ported from the repo; "
            "run `moai init` yourself later for .moai scaffolding."
        )
        clone = ensure_clone(report)
        if clone is None:
            raise RuntimeError("Could not clone moai-adk repo and no local install exists.")
        report.log(f"Using templates from clone at {clone}")
        copy_skills_from(os.path.join(clone, ".claude", "skills"), target, categories, report)
        convert_commands_from(os.path.join(clone, ".claude", "commands", "moai"), target, report)
        if not os.path.exists(os.path.join(target, "AGENTS.md")):
            tmpl = os.path.join(clone, "internal", "template", "templates", "AGENTS.md.tmpl")
            if os.path.isfile(tmpl):
                shutil.copy(tmpl, os.path.join(target, "AGENTS.md"))
                report.log("Copied AGENTS.md template (raw .tmpl; review placeholders)")
        if not os.path.exists(os.path.join(target, ".moai")):
            report.warnings.append(
                ".moai/ scaffolding missing — the ported skills expect it. "
                "Install moai-adk and run `moai init` in this project."
            )

    update_gitignore(target, report)
    return report


# ---------------------------------------------------------------------------
# Clean / uninstall (unchanged behavior)
# ---------------------------------------------------------------------------

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
                    shutil.rmtree(os.path.join(skills_dir, name), ignore_errors=True)
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
            if "moai" in head.lower():
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
        rel = CLEAN_ITEMS[key][0].split(" ")[0]
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
