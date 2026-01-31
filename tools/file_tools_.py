import os
import glob as glob_module
import re
from typing import Optional


def read_(
    file_path: str,
    *,
    project_dir: str,
    offset: int = 0,
    limit: int = 2000
) -> str:
    """Read file contents with optional line range.

    Args:
        file_path: Absolute or relative path to file
        offset: Line number to start from (0-indexed)
        limit: Max lines to read (default 2000)
    """
    # Resolve path
    if not os.path.isabs(file_path):
        file_path = os.path.join(project_dir, file_path)

    if not os.path.exists(file_path):
        return f"[File not found: {file_path}]"

    if os.path.isdir(file_path):
        return f"[Path is a directory, not a file: {file_path}]"

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        total_lines = len(lines)

        # Apply offset and limit
        selected = lines[offset:offset + limit]

        # Format with line numbers (1-indexed for display)
        output_lines = []
        for i, line in enumerate(selected):
            line_num = offset + i + 1
            # Truncate very long lines
            if len(line) > 2000:
                line = line[:2000] + "... [truncated]"
            output_lines.append(f"{line_num:6d}\t{line.rstrip()}")

        output = "\n".join(output_lines)

        if offset + limit < total_lines:
            output += f"\n\n[... {total_lines - offset - limit} more lines]"

        return output

    except Exception as e:
        return f"[Read error: {type(e).__name__}: {e}]"


def write_(
    file_path: str,
    content: str,
    *,
    project_dir: str
) -> str:
    """Write content to file (creates or overwrites).

    Args:
        file_path: Absolute or relative path
        content: Content to write
    """
    if not os.path.isabs(file_path):
        file_path = os.path.join(project_dir, file_path)

    try:
        # Create parent directories if needed
        parent = os.path.dirname(file_path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        lines = content.count("\n") + 1
        return f"Wrote {lines} lines to {file_path}"

    except Exception as e:
        return f"[Write error: {type(e).__name__}: {e}]"


def edit_(
    file_path: str,
    old_string: str,
    new_string: str,
    *,
    project_dir: str,
    replace_all: bool = False
) -> str:
    """Surgical string replacement in file.

    Finds exact match of old_string and replaces with new_string.
    Fails if old_string not found or not unique (unless replace_all=True).

    Args:
        file_path: Absolute or relative path
        old_string: Exact text to find and replace
        new_string: Replacement text
        replace_all: If True, replace all occurrences (default False)
    """
    if not os.path.isabs(file_path):
        file_path = os.path.join(project_dir, file_path)

    if not os.path.exists(file_path):
        return f"[File not found: {file_path}]"

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        count = content.count(old_string)

        if count == 0:
            return f"[String not found in {file_path}]"

        if count > 1 and not replace_all:
            return f"[String found {count} times - use replace_all=True or provide more context]"

        # Find line number(s) before replacement
        if not replace_all:
            pos = content.find(old_string)
            start_line = content[:pos].count('\n') + 1
            end_line = start_line + old_string.count('\n')

        # Perform replacement
        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        # Build return message
        if replace_all:
            return f"Replaced {count} occurrence(s) in {file_path}"
        else:
            line_info = f"line {start_line}" if start_line == end_line else f"lines {start_line}-{end_line}"
            return f"Replaced at {line_info} in {file_path}"

    except Exception as e:
        return f"[Edit error: {type(e).__name__}: {e}]"


def glob_(
    pattern: str,
    *,
    project_dir: str,
    path: str = None
) -> str:
    """Find files matching glob pattern.

    Args:
        pattern: Glob pattern (e.g., "**/*.py", "src/*.ts")
        path: Directory to search in (default: project_dir)
    """
    base_path = path if path and os.path.isabs(path) else project_dir
    if path and not os.path.isabs(path):
        base_path = os.path.join(project_dir, path)

    if not os.path.isdir(base_path):
        return f"[Directory not found: {base_path}]"

    try:
        # Use recursive glob
        full_pattern = os.path.join(base_path, pattern)
        matches = glob_module.glob(full_pattern, recursive=True)

        # Sort by modification time (newest first)
        matches.sort(key=lambda x: os.path.getmtime(x), reverse=True)

        # Limit results
        if len(matches) > 100:
            matches = matches[:100]
            truncated = True
        else:
            truncated = False

        if not matches:
            return f"No files matching '{pattern}' in {base_path}"

        output = "\n".join(matches)
        if truncated:
            output += f"\n\n[... truncated to 100 results]"

        return output

    except Exception as e:
        return f"[Glob error: {type(e).__name__}: {e}]"


def grep_(
    pattern: str,
    *,
    project_dir: str,
    path: str = None,
    file_pattern: str = None,
    ignore_case: bool = False,
    context_lines: int = 0
) -> str:
    """Search file contents with regex pattern.

    Args:
        pattern: Regex pattern to search for
        path: File or directory to search (default: project_dir)
        file_pattern: Glob pattern to filter files (e.g., "*.py")
        ignore_case: Case-insensitive search
        context_lines: Lines of context before/after match
    """
    base_path = path if path and os.path.isabs(path) else project_dir
    if path and not os.path.isabs(path):
        base_path = os.path.join(project_dir, path)

    if not os.path.exists(base_path):
        return f"[Path not found: {base_path}]"

    try:
        flags = re.IGNORECASE if ignore_case else 0
        regex = re.compile(pattern, flags)
    except re.error as e:
        return f"[Invalid regex: {e}]"

    results = []
    files_searched = 0
    matches_found = 0

    def search_file(filepath):
        nonlocal matches_found
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            file_matches = []
            for i, line in enumerate(lines):
                if regex.search(line):
                    # Gather context
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 1)

                    context = []
                    for j in range(start, end):
                        prefix = ">" if j == i else " "
                        context.append(f"{prefix}{j+1:6d}: {lines[j].rstrip()}")

                    file_matches.append("\n".join(context))
                    matches_found += 1

            if file_matches:
                results.append(f"\n{filepath}:\n" + "\n---\n".join(file_matches))

        except Exception:
            pass  # Skip unreadable files

    if os.path.isfile(base_path):
        files_searched = 1
        search_file(base_path)
    else:
        # Search directory
        glob_pat = file_pattern or "**/*"
        files = glob_module.glob(os.path.join(base_path, glob_pat), recursive=True)

        for filepath in files:
            if os.path.isfile(filepath) and matches_found < 500:
                files_searched += 1
                search_file(filepath)

    if not results:
        return f"No matches for '{pattern}' in {base_path}"

    output = "\n".join(results)

    # Truncate if too long
    if len(output) > 50000:
        output = output[:50000] + "\n\n[... output truncated]"

    return output
