import asyncio
import os


async def bash_(
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
        proc = await asyncio.create_subprocess_exec(
            "/bin/bash", "-c", command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "TERM": "dumb"},
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return f"[Command timed out after {timeout}s]"
        except asyncio.CancelledError:
            proc.kill()
            await proc.wait()
            raise  # re-raise so gather/task cancellation propagates

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        # Build output
        if proc.returncode != 0:
            output = f"[Exit code: {proc.returncode}]\n"
            if stderr:
                output += f"STDERR:\n{stderr}\n"
            if stdout:
                output += f"STDOUT:\n{stdout}"
        else:
            output = stdout
            if stderr:
                output += f"\n[STDERR]: {stderr}"

        # Truncate if too long (keep head + tail)
        if len(output) > max_output_chars:
            half = max_output_chars // 2
            output = (
                output[:half] +
                f"\n\n... [TRUNCATED {len(output) - max_output_chars} chars] ...\n\n" +
                output[-half:]
            )

        return output.strip() or "[No output]"

    except asyncio.CancelledError:
        raise  # always propagate
    except Exception as e:
        return f"[Bash error: {type(e).__name__}: {e}]"
