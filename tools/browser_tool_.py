import os
import time

_playwright = None
_browser = None
_page = None


async def _ensure_browser():
    global _playwright, _browser, _page
    if not _page:
        from playwright.async_api import async_playwright
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(headless=False)
        _page = await _browser.new_page(viewport={"width": 1280, "height": 720})
    return _page


async def close_browser():
    global _playwright, _browser, _page
    if _browser:
        await _browser.close()
    if _playwright:
        await _playwright.stop()
    _playwright = _browser = _page = None


async def browser(code: str, *, project_dir: str, end_resp: str = "Anthropic") -> str:
    """Execute Playwright Python code against a persistent browser session.

    A browser with a single page is lazily initialized on first call and persists
    across calls. `page` is pre-initialized in scope — just write Playwright code.

    Every execution auto-captures a screenshot which is analyzed via vision.

    Args:
        code: Playwright Python async code. `page` is available.
              Examples:
                await page.goto("https://example.com")
                await page.click("button#submit")
                await page.fill("input[name=q]", "hello")
    """
    page = await _ensure_browser()

    # Screenshot dir
    ss_dir = os.path.join(project_dir, ".browser_screenshots")
    os.makedirs(ss_dir, exist_ok=True)

    # Execute the code with page in scope
    exec_output = ""
    try:
        # Capture any return value from last expression
        local_ns = {"page": page}
        exec(
            f"async def __browser_exec__():\n"
            + "".join(f"    {line}\n" for line in code.strip().splitlines()),
            local_ns,
        )
        result = await local_ns["__browser_exec__"]()
        if result is not None:
            exec_output = str(result)
    except Exception as e:
        exec_output = f"Execution error: {type(e).__name__}: {e}"

    # Auto-screenshot after every execution
    screenshot_path = os.path.join(ss_dir, f"{int(time.time() * 1000)}.png")
    try:
        await page.screenshot(path=screenshot_path)
    except Exception as e:
        return f"{exec_output}\n[Screenshot failed: {e}]".strip()

    # Vision the screenshot
    from tools.vision_tools_ import vision
    description = await vision(
        screenshot_path,
        "Describe what you see on this browser page. Note key UI elements, text content, forms, buttons, errors, and layout.",
        project_dir=project_dir,
        end_resp=end_resp,
    )

    parts = []
    if exec_output:
        parts.append(f"Output: {exec_output}")
    parts.append(f"Screenshot: {screenshot_path}")
    parts.append(f"Page: {description}")

    return "\n".join(parts)
