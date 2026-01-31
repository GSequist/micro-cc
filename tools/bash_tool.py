import subprocess
import os


def bash_(
    command: str,
    *,
    project_dir: str,
    path: str = None,
    timeout: int = 120
) -> str:
    """Execute bash command.

    Args:
        command: The bash command to execute
        path: Working directory (default: project_dir). Relative paths resolve to project_dir.
        timeout: Max seconds before killing process (default 120)
    """
    max_output_chars = 60000

    # Determine cwd
    if path:
        if os.path.isabs(path):
            cwd = path
        else:
            cwd = os.path.normpath(os.path.join(project_dir, path))
    else:
        cwd = project_dir

    if not os.path.isdir(cwd):
        return f"[Directory not found: {cwd}]"

    try:
        result = subprocess.run(
            ["/bin/bash", "-c", command],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "TERM": "dumb"}
        )

        # Build output
        if result.returncode != 0:
            output = f"[Exit code: {result.returncode}]\n"
            if result.stderr:
                output += f"STDERR:\n{result.stderr}\n"
            if result.stdout:
                output += f"STDOUT:\n{result.stdout}"
        else:
            output = result.stdout
            if result.stderr:
                output += f"\n[STDERR]: {result.stderr}"

        # Truncate if too long (keep head + tail)
        if len(output) > max_output_chars:
            half = max_output_chars // 2
            output = (
                output[:half] +
                f"\n\n... [TRUNCATED {len(output) - max_output_chars} chars] ...\n\n" +
                output[-half:]
            )

        return output.strip() or "[No output]"

    except subprocess.TimeoutExpired:
        return f"[Command timed out after {timeout}s]"

    except Exception as e:
        return f"[Bash error: {type(e).__name__}: {e}]"
