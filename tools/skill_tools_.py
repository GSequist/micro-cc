"""Skill Tools - Tools for reading and using agent skills."""

from skills.skill_loader import (
    get_skill_content,
    get_skill_path,
    list_skill_files,
    get_available_skills,
)
from pathlib import Path


def read_skill(skill_name: str, *, project_dir) -> str:
    """Load skill instructions. Supports main skills and subskills.

    Args:
        skill_name: Skill path. Examples:
            - "docx" - loads main SKILL.md
            - "docx/ooxml.md" - loads subskill
    """
    # Check if requesting a subskill
    if "/" in skill_name or skill_name.endswith(".md"):
        return _read_subskill(skill_name)

    # Main skill - load SKILL.md
    content = get_skill_content(skill_name)

    if not content:
        available = get_available_skills()
        skill_names = [s["name"] for s in available]
        return f"Skill '{skill_name}' not found. Available: {', '.join(skill_names)}"

    skill_path = get_skill_path(skill_name)
    files = list_skill_files(skill_name)
    md_files = [f for f in files if f.endswith(".md") and f != "SKILL.md"]

    response_parts = [
        f"# Skill: {skill_name}",
        "",
        content,
        "",
        "---",
        f"**Skill location**: {skill_path}",
    ]

    if md_files:
        response_parts.append("**Related documentation** (use read_skill to load):")
        for f in md_files:
            response_parts.append(f"  - {skill_name}/{f}")

    return "\n".join(response_parts)


def _read_subskill(skill_path: str) -> str:
    """Read a subskill file within a skill folder."""
    parts = skill_path.split("/", 1)
    if len(parts) == 1:
        base_skill = parts[0].replace(".md", "")
        subfile = parts[0]
    else:
        base_skill = parts[0]
        subfile = parts[1]

    base_path = get_skill_path(base_skill)
    if not base_path:
        available = get_available_skills()
        skill_names = [s["name"] for s in available]
        return f"Skill '{base_skill}' not found. Available: {', '.join(skill_names)}"

    full_path = Path(base_path) / subfile
    if not full_path.exists():
        files = list_skill_files(base_skill)
        md_files = [f for f in files if f.endswith(".md")]
        return f"Subskill '{subfile}' not found in {base_skill}. Available: {', '.join(md_files)}"

    content = full_path.read_text(encoding="utf-8")
    return f"# {base_skill}/{subfile}\n\n{content}"


def list_skills(*, project_dir) -> str:
    """List all available skills with their descriptions."""
    skills = get_available_skills()

    if not skills:
        return "No skills available."

    lines = ["# Available Skills", ""]
    for skill in skills:
        lines.append(f"**{skill['name']}**")
        lines.append(f"{skill['description']}")
        lines.append("")

    return "\n".join(lines)
