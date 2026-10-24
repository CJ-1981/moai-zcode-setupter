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
SETUPTER_VERSION = "0.5.0"

# Title of the 0.34-era CLAUDE.md; used to detect a legacy file that v3 init
# preserved instead of replacing.
LEGACY_CLAUDE_MARKER = "Mr. Alfred Execution Directive"


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
    """Return (kind, version): kind in {"v3", "legacy", None}.

    Explicit ~/.local/bin paths are checked because GUI apps launched from
    Finder get a minimal PATH that doesn't include it.
    """
    kind, version, _ = _locate_moai()
    return kind, version


def _locate_moai() -> tuple[str | None, str, str | None]:
    """Return (kind, version, binary path)."""
    moai_candidates = [shutil.which("moai"), os.path.expanduser("~/.local/bin/moai")]
    if os.name == "nt":
        moai_candidates += [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\moai\moai.exe"),
            os.path.expanduser("~/.local/bin/moai.exe"),
        ]
    candidates = {
        "moai": moai_candidates,
        "moai-adk": [shutil.which("moai-adk"), LEGACY_MOAI_BIN],
    }
    for name, paths in candidates.items():
        path = next((p for p in paths if p and os.path.isfile(p) and os.access(p, os.X_OK)), None)
        if not path:
            continue
        try:
            proc = _run([path, "--version"])
        except Exception:
            continue
        m = re.search(r"(\d+\.\d+(?:\.\d+)?)", proc.stdout + proc.stderr)
        if not m:
            continue
        major = int(m.group(1).split(".")[0])
        version = m.group(1)
        if major >= 2:
            return "v3", version, path
        if name == "moai-adk":
            return "legacy", version, path
    return None, "", None


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", v)[:3])


def get_latest_moai_version() -> str | None:
    """Latest moai-adk release tag from the GitHub API (None if unreachable)."""
    import json
    import urllib.request

    url = "https://api.github.com/repos/modu-ai/moai-adk/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": "moai-zcode-setupter"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            tag = json.load(resp).get("tag_name", "")
    except Exception:
        return None
    return tag.lstrip("v") or None


def moai_install_cmd() -> list[str]:
    """Cross-platform official installer command."""
    if os.name == "nt":
        return [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
            "irm https://adk.moai.kr/install.ps1 | iex",
        ]
    return ["bash", "-c", "curl -fsSL https://adk.mo.ai.kr/install.sh | bash"]


def install_or_update_moai(report: SetupReport) -> bool:
    """Run the official moai-adk installer. Returns True on success."""
    before = _locate_moai()[1]
    report.log(f"Running moai-adk installer (currently: {before or 'not installed'}) ...")
    try:
        proc = _run(moai_install_cmd(), timeout=600)
    except Exception as exc:
        report.warnings.append(f"Installer failed to run: {exc}")
        return False
    out = (proc.stdout + proc.stderr).strip()
    if out:
        report.log(f"installer output (tail):\n{out[-1200:]}")
    after = _locate_moai()
    if after[0] != "v3":
        report.warnings.append(
            "Installer finished but no v3 moai binary detected afterwards; "
            "you may need to open a new terminal so PATH refreshes."
        )
        return False
    report.log(f"moai-adk now: {after[1]}")
    return True


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
    "workflow": "moai-workflow-* (spec/testing workflow + core agents) — required",
    "domain": "moai-domain-* (backend/frontend/database/design)",
    "ref": "moai-ref-* (reference playbooks: security, testing, patterns)",
    # v3 no longer ships these; kept for legacy (v0.x) installs only.
    "lang": "moai-lang-* (languages, legacy v0.x only)",
    "platform": "moai-platform-* (supabase/vercel/..., legacy v0.x only)",
    "library": "moai-library-* (mermaid/nextra/..., legacy v0.x only)",
    "mcp": "moai-mcp-* (figma/notion, legacy v0.x only)",
}
REQUIRED_CATEGORIES = {"foundation", "workflow"}

# Core single-instance v3 skills (no category prefix beyond "moai-");
# always included alongside the required categories.
CORE_SINGLE_SKILLS = {
    "moai",
    "moai-factory-foreman",
    "moai-harness-learner",
    "moai-jev-skill-suggestion",
    "moai-lane-watchdog",
    "moai-meta-harness",
}


def _skill_category(name: str) -> str | None:
    if not name.startswith("moai-"):
        return None
    if name in CORE_SINGLE_SKILLS:
        return "workflow"
    rest = name.removeprefix("moai-")
    for cat in SKILL_CATEGORIES:
        if rest.startswith(cat + "-"):
            return cat
    return None


def copy_skills_from(
    src_root: str, target: str, categories: set[str], report: SetupReport,
    dst_subdir: str = os.path.join(".zcode", "skills"),
) -> None:
    """Copy moai skills from a .claude/skills (or templates) root.

    Also removes stale moai-* skills in the destination that no longer exist
    in the source (e.g. 0.34-era skills after upgrading to v3).
    """
    dst_root = os.path.join(target, *dst_subdir.split("/"))
    if not os.path.isdir(src_root):
        report.warnings.append(f"Skills source not found: {src_root}")
        return
    source_names = set()
    for name in sorted(os.listdir(src_root)):
        cat = _skill_category(name)
        if cat is None:  # uncategorized (e.g. the "moai" dispatcher is handled too)
            cat = "workflow" if name == "moai" else None
        if cat is None:
            continue
        if cat not in categories and cat not in REQUIRED_CATEGORIES:
            continue
        source_names.add(name)
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
    # Drop stale moai-* skills (from an older moai-adk generation) that the
    # current source no longer ships. Never touches non-moai skills.
    stale = []
    if os.path.isdir(dst_root):
        for name in sorted(os.listdir(dst_root)):
            if name.startswith("moai-") and name not in source_names:
                shutil.rmtree(os.path.join(dst_root, name), ignore_errors=True)
                stale.append(name)
    if stale:
        report.log(f"Removed {len(stale)} stale skills: {', '.join(stale)}")
    report.log(f"Copied {len(report.copied_skills)} skills to {dst_subdir}")


def convert_commands_from(
    src_root: str, target: str, report: SetupReport,
    dst_subdir: str = os.path.join(".zcode", "skills"),
) -> None:
    dst_root = os.path.join(target, *dst_subdir.split("/"))
    if not os.path.isdir(src_root):
        report.warnings.append(f"Commands source not found: {src_root}")
        return
    converted = set()
    for filename in sorted(os.listdir(src_root)):
        if not filename.endswith(".md"):
            continue
        # 0.34-era numbered commands (0-project.md, 1-plan.md, ...) that v3
        # init preserved in the project; their v3 replacements exist.
        if re.match(r"^\d+-", filename):
            report.log(f"Skipped legacy 0.34 command file: {filename}")
            continue
        with open(os.path.join(src_root, filename)) as f:
            text = f.read()
        slug = filename.removesuffix(".md")
        skill_dir = os.path.join(dst_root, f"moai-cmd-{slug}")
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write(convert_command(text, filename))
        report.converted_commands.append(f"moai-cmd-{slug}")
        converted.add(f"moai-cmd-{slug}")
    # Drop stale moai-cmd-* skills (e.g. 0.34-era numbered commands).
    stale = []
    if os.path.isdir(dst_root):
        for name in sorted(os.listdir(dst_root)):
            if name.startswith("moai-cmd-") and name not in converted:
                shutil.rmtree(os.path.join(dst_root, name), ignore_errors=True)
                stale.append(name)
    if stale:
        report.log(f"Removed {len(stale)} stale command skills: {', '.join(stale)}")
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

# Ported skills secrets (mirror moai-adk's .claude/skills rules)
.zcode/skills/*/.env*
.zcode/skills/*/secrets/
.zcode/skills/*/*/*secret*
.zcode/skills/*/*/*key*
.zcode/skills/*/*/*token*
.agents/skills/*/.env*
.agents/skills/*/secrets/
.agents/skills/*/*/*secret*
.agents/skills/*/*/*key*
.agents/skills/*/*/*token*
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


AGENTS_NOTE = (
    "<!-- Derived from CLAUDE.md by MoAI-ZCode-Setupter for ZCode. `/moai <cmd>` "
    "slash commands map to the `moai-cmd-*` skills in .zcode/skills/. -->\n\n"
)


def derive_agents_md(target: str, report: SetupReport) -> None:
    """(Re)build AGENTS.md from the project's CLAUDE.md if it is stale."""
    agents = os.path.join(target, "AGENTS.md")
    claude = os.path.join(target, "CLAUDE.md")
    if not os.path.isfile(claude):
        return
    with open(claude) as f:
        text = f.read()
    if os.path.isfile(agents):
        with open(agents) as f:
            current = f.read()
        if LEGACY_CLAUDE_MARKER not in text and LEGACY_CLAUDE_MARKER not in current:
            report.log("AGENTS.md already present and current; left as is.")
            return
    with open(agents, "w") as f:
        f.write(AGENTS_NOTE + text)
    report.log("Created/refreshed AGENTS.md (derived from project CLAUDE.md)")


def refresh_legacy_claude_md(target: str, binpath: str, report: SetupReport) -> None:
    """If v3 init preserved a 0.34-era CLAUDE.md, replace it with the v3 one.

    v3 init keeps an existing CLAUDE.md, so projects ported in the 0.34 days
    keep outdated instructions. The old file is backed up as
    CLAUDE.md.moai034.bak before replacement.
    """
    claude = os.path.join(target, "CLAUDE.md")
    if not os.path.isfile(claude):
        return
    with open(claude) as f:
        text = f.read()
    if LEGACY_CLAUDE_MARKER not in text:
        return
    import tempfile

    with tempfile.TemporaryDirectory(prefix="moai-fresh-") as tmp:
        _run(["git", "init", "-q"], cwd=tmp)
        proc = _run([binpath, "init", "--non-interactive"], cwd=tmp)
        fresh = os.path.join(tmp, "CLAUDE.md")
        if proc.returncode != 0 or not os.path.isfile(fresh):
            report.warnings.append(
                "Project CLAUDE.md is the legacy 0.34 version and a fresh v3 "
                "copy could not be generated; left as is."
            )
            return
        backup = os.path.join(target, "CLAUDE.md.moai034.bak")
        if not os.path.exists(backup):
            shutil.copy2(claude, backup)
        shutil.copy2(fresh, claude)
    report.log(
        "Replaced legacy 0.34 CLAUDE.md with v3 version (old file: CLAUDE.md.moai034.bak)"
    )


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


# Skill destination subdirs (relative to project root) per tool target.
SKILL_TARGET_DIRS = {
    "zcode": os.path.join(".zcode", "skills"),
    "antigravity": os.path.join(".agents", "skills"),
}


def setup_project(
    target: str, categories: set[str], skill_targets: set[str] | None = None
) -> SetupReport:
    skill_targets = skill_targets or {"zcode"}
    unknown = skill_targets - set(SKILL_TARGET_DIRS)
    if unknown:
        raise ValueError(f"Unknown skill targets: {sorted(unknown)}")
    dst_subdirs = [SKILL_TARGET_DIRS[t] for t in sorted(skill_targets)]
    report = SetupReport(target=target)
    if not os.path.isdir(target):
        raise NotADirectoryError(target)
    if not os.path.exists(os.path.join(target, ".git")):
        report.warnings.append("Target is not a git repository root (no .git found).")

    kind, version, binpath = _locate_moai()

    if kind == "v3":
        report.log(f"Found moai-adk v3 binary ({version}); running moai init ...")
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
        if os.path.isdir(src_skills) or os.path.isdir(user_skills):
            src = src_skills if os.path.isdir(src_skills) else user_skills
            if src == user_skills:
                report.log("Project had no skills; using ~/.claude/skills")
            for d in dst_subdirs:
                copy_skills_from(src, target, categories, report, dst_subdir=d)
        else:
            report.warnings.append("No .claude/skills in project after init.")
        for d in dst_subdirs:
            convert_commands_from(src_cmds, target, report, dst_subdir=d)
        refresh_legacy_claude_md(target, binpath, report)
        derive_agents_md(target, report)
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
        for d in dst_subdirs:
            copy_skills_from(
                os.path.join(templates, ".claude", "skills"), target, categories, report, dst_subdir=d
            )
            convert_commands_from(
                os.path.join(templates, ".claude", "commands", "moai"), target, report, dst_subdir=d
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
        for d in dst_subdirs:
            copy_skills_from(
                os.path.join(clone, ".claude", "skills"), target, categories, report, dst_subdir=d
            )
            convert_commands_from(
                os.path.join(clone, ".claude", "commands", "moai"), target, report, dst_subdir=d
            )
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
# Suspend / resume moai in Claude Code (context-pressure relief)
# ---------------------------------------------------------------------------

# Files renamed to <name>.off while suspended so Claude Code's startup scan
# skips them. Hooks/settings/commands stay active (light bypass).
# AGENTS.md is deliberately NOT suspended: ZCode reads it, and keeping it
# costs Claude only ~20k while leaving ZCode's moai support fully intact.
SUSPEND_PATHS = (
    ".claude/rules",
    "CLAUDE.md",
)


def claude_suspended(target: str) -> bool:
    """True if any suspend marker (.off) exists in the project."""
    return any(
        os.path.exists(os.path.join(target, p + ".off")) for p in SUSPEND_PATHS
    )


def claude_running() -> bool:
    """Best-effort check for a running Claude Code process."""
    try:
        proc = subprocess.run(
            ["pgrep", "-fl", "claude"], capture_output=True, text=True, timeout=10
        )
        return proc.returncode == 0
    except Exception:
        return False


def suspend_moai_claude(target: str, report: SetupReport) -> None:
    if claude_suspended(target):
        report.warnings.append("Already suspended; nothing to do.")
        return
    for p in SUSPEND_PATHS:
        src = os.path.join(target, p)
        if os.path.exists(src):
            os.rename(src, src + ".off")
            report.log(f"Renamed {p} -> {p}.off")
    report.log(
        "Suspended. Start a NEW Claude Code session to get the light context; "
        "rename back (Resume button) before doing moai workflow work."
    )


def resume_moai_claude(target: str, report: SetupReport) -> None:
    if not claude_suspended(target):
        report.warnings.append("Not suspended; nothing to do.")
        return
    for p in SUSPEND_PATHS:
        src = os.path.join(target, p)
        off = src + ".off"
        if os.path.exists(off):
            os.rename(off, src)
            report.log(f"Renamed {p}.off -> {p}")
    report.log("Resumed. New Claude Code sessions load the full moai instructions again.")


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
    return name == "moai" or name.startswith("moai-")


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
        removed = 0
        for root_rel in ((".zcode", "skills"), (".agents", "skills")):
            skills_dir = os.path.join(target, *root_rel)
            if os.path.isdir(skills_dir):
                for name in sorted(os.listdir(skills_dir)):
                    if _skill_is_moai(name):
                        shutil.rmtree(os.path.join(skills_dir, name), ignore_errors=True)
                        removed += 1
                        report.copied_skills.append(name)
                if not os.listdir(skills_dir):
                    os.rmdir(skills_dir)
                    parent = os.path.join(target, root_rel[0])
                    if os.path.isdir(parent) and not os.listdir(parent):
                        os.rmdir(parent)
        report.log(f"Removed {removed} moai-* skills from .zcode/skills/ and .agents/skills/")

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
