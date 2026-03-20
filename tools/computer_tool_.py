import os
import subprocess
import time

import pyautogui

# Safety: disable pyautogui's fail-safe (move mouse to corner to abort)
# Keep it ON by default — user can disable in their code if needed
pyautogui.PAUSE = 0.3


async def computer(code: str, *, project_dir: str, end_resp: str = "Anthropic") -> str:
    """Execute PyAutoGUI code to control the Mac desktop.

    `pyautogui` and `time` are pre-imported. Use for mouse, keyboard, and screen control.

    Every execution auto-captures a screenshot which is analyzed via vision.

    Args:
        code: Python code using pyautogui. Examples:
              pyautogui.click(500, 300)
              pyautogui.hotkey('cmd', 'space')
              pyautogui.write('hello', interval=0.05)
              pyautogui.moveTo(100, 200)
              pyautogui.scroll(-3)
    """
    # Screenshot dir
    ss_dir = os.path.join(project_dir, ".computer_screenshots")
    os.makedirs(ss_dir, exist_ok=True)

    # Execute the code with pyautogui in scope
    exec_output = ""
    try:
        local_ns = {"pyautogui": pyautogui, "time": time}
        exec(
            f"async def __computer_exec__():\n"
            + "".join(f"    {line}\n" for line in code.strip().splitlines()),
            local_ns,
        )
        result = await local_ns["__computer_exec__"]()
        if result is not None:
            exec_output = str(result)
    except Exception as e:
        exec_output = f"Execution error: {type(e).__name__}: {e}"

    # Auto-screenshot via native screencapture (faster + better quality than pyautogui)
    screenshot_path = os.path.join(ss_dir, f"{int(time.time() * 1000)}.png")
    try:
        subprocess.run(
            ["screencapture", "-x", screenshot_path],
            timeout=5,
        )
    except Exception as e:
        return f"{exec_output}\n[Screenshot failed: {e}]".strip()

    # Vision the screenshot
    from tools.vision_tools_ import vision
    description = await vision(
        screenshot_path,
        "Describe what you see on this Mac screen. Note key UI elements, windows, text content, buttons, menus, and cursor position.",
        project_dir=project_dir,
        end_resp=end_resp,
    )

    parts = []
    if exec_output:
        parts.append(f"Output: {exec_output}")
    parts.append(f"Screenshot: {screenshot_path}")
    parts.append(f"Screen: {description}")

    return "\n".join(parts)
