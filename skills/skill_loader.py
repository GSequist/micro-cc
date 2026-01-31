"""
Skill Loader - Parses YAML frontmatter from SKILL.md files and provides skill discovery.

Progressive disclosure pattern:
1. Level 1: Skill metadata (name+description) injected into system prompt
2. Level 2: Agent calls read_skill() to load full SKILL.md content
3. Level 3: Agent reads reference files or executes scripts via kernel
"""

import os
import re
from pathlib import Path
from typing import Optional

# Cache for loaded skills
_skills_cache: dict = {}
_cache_initialized = False

SKILLS_DIR = Path(__file__).parent


def _parse_yaml_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown content.

    Returns:
        tuple of (frontmatter_dict, remaining_content)
    """
    frontmatter = {}
    body = content

    # Match YAML frontmatter between --- markers
    pattern = r'^---\s*\n(.*?)\n---\s*\n(.*)$'
    match = re.match(pattern, content, re.DOTALL)

    if match:
        yaml_content = match.group(1)
        body = match.group(2)

        # Simple YAML parsing (name: value pairs)
        for line in yaml_content.split('\n'):
            line = line.strip()
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip()
                # Remove quotes if present
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    value = value[1:-1]
                frontmatter[key] = value

    return frontmatter, body


def _load_skills():
    """Scan skills/ folder and load all SKILL.md frontmatter."""
    global _skills_cache, _cache_initialized

    if _cache_initialized:
        return

    _skills_cache = {}

    if not SKILLS_DIR.exists():
        _cache_initialized = True
        return

    for skill_dir in SKILLS_DIR.iterdir():
        if not skill_dir.is_dir():
            continue

        skill_file = skill_dir / 'SKILL.md'
        if not skill_file.exists():
            continue

        try:
            content = skill_file.read_text(encoding='utf-8')
            frontmatter, body = _parse_yaml_frontmatter(content)

            name = frontmatter.get('name', skill_dir.name)
            description = frontmatter.get('description', '')

            _skills_cache[name] = {
                'name': name,
                'description': description,
                'path': str(skill_dir),
                'skill_file': str(skill_file),
                'full_content': content,
                'body': body,
            }
        except Exception as e:
            print(f"Error loading skill from {skill_dir}: {e}")

    _cache_initialized = True


def get_available_skills() -> list[dict]:
    """Get list of available skills with name and description.

    Returns:
        List of dicts with 'name' and 'description' keys.
    """
    _load_skills()
    return [
        {'name': skill['name'], 'description': skill['description']}
        for skill in _skills_cache.values()
    ]


def get_skill_summary() -> str:
    """Get formatted summary of all skills for injection into system prompt.

    Returns:
        Formatted string listing all available skills.
    """
    _load_skills()

    if not _skills_cache:
        return ""

    lines = ["Available skills (use `read_skill` tool to load full instructions):"]
    for skill in _skills_cache.values():
        lines.append(f"- **{skill['name']}**: {skill['description']}")

    return "\n".join(lines)


def get_skill_content(skill_name: str) -> Optional[str]:
    """Get the full content of a skill's SKILL.md file.

    Args:
        skill_name: Name of the skill to load.

    Returns:
        Full SKILL.md content or None if skill not found.
    """
    _load_skills()

    skill = _skills_cache.get(skill_name)
    if skill:
        return skill['body']  # Return body without frontmatter
    return None


def get_skill_path(skill_name: str) -> Optional[str]:
    """Get the filesystem path to a skill's folder.

    Args:
        skill_name: Name of the skill.

    Returns:
        Path to skill folder or None if not found.
    """
    _load_skills()

    skill = _skills_cache.get(skill_name)
    if skill:
        return skill['path']
    return None


def list_skill_files(skill_name: str) -> list[str]:
    """List all files in a skill's folder.

    Args:
        skill_name: Name of the skill.

    Returns:
        List of relative file paths within the skill folder.
    """
    _load_skills()

    skill = _skills_cache.get(skill_name)
    if not skill:
        return []

    skill_path = Path(skill['path'])
    files = []

    for item in skill_path.rglob('*'):
        if item.is_file():
            files.append(str(item.relative_to(skill_path)))

    return files


def reload_skills():
    """Force reload of skills cache. Useful after adding new skills."""
    global _cache_initialized
    _cache_initialized = False
    _load_skills()
