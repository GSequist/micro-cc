import json
import time
from cache.redis_cache import RedisStateManager

def make_plan(content: str, *, project_path: str) -> str:
    """Create a new plan with task IDs and initial status.

    Use when user asks something fundamentally different from the existing plan.
    Don't use to update existing plan - use update_step instead.

    Args:
        content: JSON string with plan structure. Format:
            {"title": "Plan Title", "steps": ["Step 1", "Step 2", ...],
             "context": "Additional context about the overall goal"}.
            CRITICAL: For tasks involving multiple files, create a separate
            step for each source to prevent information loss.
    """
    redis_state = RedisStateManager()

    if not content:
        return "Error: Content cannot be empty"

    try:
        plan_data = json.loads(content)

        if "title" not in plan_data or "steps" not in plan_data:
            return "Error: Plan must have 'title' and 'steps' fields"

        if not isinstance(plan_data["steps"], list) or len(plan_data["steps"]) == 0:
            return "Error: Steps must be a non-empty list"

        structured_plan = {
            "title": plan_data["title"],
            "context": plan_data.get("context", ""),
            "steps": plan_data["steps"],
            "step_statuses": ["not_started"] * len(plan_data["steps"]),
            "step_findings": [""] * len(plan_data["steps"]),
            "current_step_index": 0,
            "created_at": time.time(),
        }

        prev_plan = redis_state.get_plan(project_path)
        new_content = json.dumps(structured_plan)
        redis_state.set_plan(project_path, new_content)

        formatted_plan = format_structured_plan(structured_plan)

        diff = ""
        if prev_plan:
            diff = f"\n\nPrevious plan replaced with new structured plan."

        return f"Structured plan created successfully\n\n{formatted_plan}{diff}"

    except json.JSONDecodeError:
        return "Error: Invalid JSON format"


from typing import Literal


def update_step(
    step_index: int = None,
    status: Literal["not_started", "in_progress", "completed", "blocked"] = None,
    findings: str = None,
    *,
    project_path,
) -> str:
    """Update specific step status and findings after completing work.

    Args:
        step_index: Step number to update (0-based). If not provided, updates
            current active step. Must be an integer.
        status: New status for the step.
        findings: New findings for this step. IMPORTANT: Include ALL previous
            findings plus new ones - otherwise they get overwritten.
    """
    redis_state = RedisStateManager()

    plan_data = redis_state.get_plan(project_path)
    if not plan_data:
        return "Error: No plan exists. Create a plan first."

    try:
        plan = json.loads(plan_data)

        if step_index is None:
            step_index = plan.get("current_step_index", 0)
        else:
            if isinstance(step_index, str):
                try:
                    step_index = int(step_index)
                except ValueError:
                    return f"Error: step_index must be a valid integer, got '{step_index}'"

        if step_index < 0 or step_index >= len(plan["steps"]):
            return f"Error: Invalid step index {step_index}. Valid range: 0-{len(plan['steps'])-1}"

        if status:
            if status not in ["not_started", "in_progress", "completed", "blocked"]:
                return "Error: Invalid status. Use: not_started, in_progress, completed, blocked"
            plan["step_statuses"][step_index] = status

            if status == "completed" and step_index == plan.get(
                "current_step_index", 0
            ):
                next_step = step_index + 1
                if next_step < len(plan["steps"]):
                    plan["current_step_index"] = next_step
                else:
                    plan["current_step_index"] = len(plan["steps"])

        if findings:
            plan["step_findings"][step_index] = findings

        updated_plan = json.dumps(plan)
        redis_state.set_plan(project_path, updated_plan)

        return f"Step {step_index} updated successfully. The plan is saved. Continue with your next action based on the plan's focus."

    except json.JSONDecodeError:
        return "Error: Invalid plan format"


def add_step(step_description: str, *, project_path) -> str:
    """Add a new step to the end of the existing plan.

    Use when research reveals additional sources or tasks needed.

    Args:
        step_description: Description of the new step. Be specific about
            what needs to be done.
    """
    redis_state = RedisStateManager()

    plan_data = redis_state.get_plan(project_path)
    if not plan_data:
        return "Error: No plan exists. Create a plan first."

    try:
        plan = json.loads(plan_data)

        # Always append to end - agents can't see full plan to make positioning decisions
        position = len(plan["steps"])

        # Insert new step and maintain array synchronization
        plan["steps"].append(step_description)
        plan["step_statuses"].append("not_started")
        plan["step_findings"].append("")

        # No need to adjust current_step_index since we're appending to end

        updated_plan = json.dumps(plan)
        redis_state.set_plan(project_path, updated_plan)

        # Show progress update
        total_steps = len(plan["steps"])
        current_step = plan.get("current_step_index", 0) + 1

        return f"✅ New step added: '{step_description}'\n📊 Plan now has {total_steps} steps total.\n🎯 Continue with current step {current_step}."

    except json.JSONDecodeError:
        return "Error: Invalid plan format"


def advance_to_step(step_index: int, *, user_id: str, conversation_id: int) -> str:
    """Move focus to a specific step and mark it as in_progress.

    Args:
        step_index: Step number to focus on (0-based). Must be an integer.
    """
    redis_state = RedisStateManager()

    plan_data = redis_state.get_plan(user_id, conversation_id)
    if not plan_data:
        return "Error: No plan exists. Create a plan first."

    try:
        plan = json.loads(plan_data)

        if isinstance(step_index, str):
            try:
                step_index = int(step_index)
            except ValueError:
                return f"Error: step_index must be a valid integer, got '{step_index}'"

        if step_index < 0 or step_index >= len(plan["steps"]):
            return f"Error: Invalid step index {step_index}. Valid range: 0-{len(plan['steps'])-1}"

        plan["current_step_index"] = step_index
        plan["step_statuses"][step_index] = "in_progress"

        updated_plan = json.dumps(plan)
        redis_state.set_plan(user_id, conversation_id, updated_plan)

        return f"Advanced to step {step_index}. The plan is saved. Continue with your next action based on the plan's focus."

    except json.JSONDecodeError:
        return "Error: Invalid plan format"


def format_structured_plan(plan: dict) -> str:
    """Format structured plan with focus on current step and progress"""
    if not plan:
        return "No plan available"

    title = plan.get("title", "Untitled Plan")
    context = plan.get("context", "")
    steps = plan.get("steps", [])
    statuses = plan.get("step_statuses", [])
    findings = plan.get("step_findings", [])
    current_index = plan.get("current_step_index", 0)

    completed = sum(1 for status in statuses if status == "completed")
    total = len(steps)
    progress_pct = (completed / total * 100) if total > 0 else 0

    formatted = f"## 📋 {title}\n\n"

    if context:
        formatted += f"**Context:** {context}\n\n"

    formatted += (
        f"**Progress:** {completed}/{total} steps completed ({progress_pct:.1f}%)\n\n"
    )

    if current_index < len(steps):
        formatted += f"**🎯 CURRENT FOCUS:** Step {current_index + 1}\n\n"

    formatted += "**Steps:**\n"

    for i, (step, status) in enumerate(zip(steps, statuses)):
        status_emoji = {
            "completed": "✅",
            "in_progress": "🔄",
            "blocked": "❌",
            "not_started": "⏳",
        }.get(status, "⏳")

        # Highlight current step
        current_marker = " ← **CURRENT**" if i == current_index else ""

        formatted += f"{i + 1}. {status_emoji} {step}{current_marker}\n"

        # Show findings for current and recently completed steps
        if findings[i] and (i == current_index or status == "completed"):
            formatted += f"   📝 *Findings:* {findings[i][:200]}{'...' if len(findings[i]) > 200 else ''}\n"

    return formatted


def get_contextual_plan_reminder(plan: dict) -> str:
    """Generate contextual reminder based on current plan state"""
    if not plan:
        return ""

    current_index = plan.get("current_step_index", 0)
    steps = plan.get("steps", [])
    statuses = plan.get("step_statuses", [])
    findings = plan.get("step_findings", [])

    if current_index >= len(steps):
        return "🎉 All plan steps completed! Use '<show_full_plan>' to review every step and then provide your final summary."

    current_step = steps[current_index]
    current_status = statuses[current_index]
    current_findings = findings[current_index]

    # Count remaining work
    remaining = sum(1 for status in statuses[current_index:] if status != "completed")

    reminder = f"🎯 **FOCUS ON STEP {current_index + 1}:** {current_step}\n"
    reminder += f"Status: {current_status.upper()} | {remaining} steps remaining\n\n"

    if current_findings:
        reminder += f"**Previous findings for this step:** {current_findings}\n\n"

    # Show what's next
    if current_index + 1 < len(steps):
        reminder += f"**Next step:** {steps[current_index + 1]}\n\n"

    reminder += (
        "Remember to use 'update_step' tool to record your findings before moving on! "
        "You can always use 'show_full_plan' to review all steps and findings at any point."
    )

    return reminder


def show_full_plan(*, user_id: str, conversation_id: int) -> str:
    """Show the complete plan with all steps and findings so far. Use this before creating final reports or deliverables to ensure you have full context."""
    redis_state = RedisStateManager()

    plan_data = redis_state.get_plan(user_id, conversation_id)
    if not plan_data:
        return "No plan exists"

    try:
        plan = json.loads(plan_data)
        formatted_plan = format_structured_plan(plan)
        return f"📋 FULL PLAN AND FINDINGS:\n\n{formatted_plan}"

    except json.JSONDecodeError:
        return "Error: Invalid plan format"


def get_post_tool_plan_reminder(plan_data: str, tool_name: str) -> str:
    if not plan_data:
        return ""

    try:
        plan = json.loads(plan_data)
        current_index = plan.get("current_step_index", 0)
        steps = plan.get("steps", [])
        findings = plan.get("step_findings", [])
        current_status = (
            plan.get("step_statuses", [])[current_index]
            if current_index < len(plan.get("step_statuses", []))
            else "unknown"
        )

        if current_index >= len(steps):
            return "[Internal planning note: 🎉 **ALL STEPS COMPLETE!** Use 'show_full_plan' to review the full plan and then provide a final summary.]"

        current_step = steps[current_index]
        current_findings = (
            findings[current_index] if current_index < len(findings) else ""
        )

        reminder = f'[Internal planning note: 🎯 **PLAN FOCUS:** You just used `{tool_name}` while working on Step {current_index + 1}: "{current_step}"'

        if current_findings:
            reminder += f"\n📝 **EXISTING FINDINGS:** {current_findings}"
            reminder += f"\n📋 **NEXT ACTION:** Use 'update_step' to ADD new findings to existing ones but make sure to include ALL previous existing findings plus new ones - otherwise you overwrite the previous findings and they get lost from your memory.  Even if the tool results are not satisfactory, write down what us your finding from them."
        else:
            reminder += f"\n📋 **NEXT ACTION:** Use 'update_step' to record findings from this tool result. Even if the tool results are not satisfactory, write down what us your finding from them."

        reminder += f"\n🔄 **STEP STATUS:** {current_status.upper()}"

        if current_status == "in_progress":
            reminder += f"\n✅ **TIP:** If this completes Step {current_index + 1}, mark status as 'completed' in update_step"

        reminder += "]"
        reminder += "remember to explicitly explain to user what tool u used and what you found out NOW user does not see and you need to document to her what you are doing!"
        return reminder

    except json.JSONDecodeError:
        return "[Internal planning note: **REMEMBER** to update your progress using 'update_step' tool.]"


def check_plan_completion(plan: dict) -> tuple[bool, str]:
    """Check if plan is complete and return status message"""
    if not plan:
        return False, "No plan exists"

    statuses = plan.get("step_statuses", [])
    steps = plan.get("steps", [])

    if not statuses or not steps:
        return False, "Invalid plan structure"

    completed = sum(1 for status in statuses if status == "completed")
    blocked = sum(1 for status in statuses if status == "blocked")
    total = len(statuses)

    if completed == total:
        return True, f"✅ Plan fully completed! All {total} steps finished."

    if blocked > 0:
        blocked_steps = [
            i + 1 for i, status in enumerate(statuses) if status == "blocked"
        ]
        return (
            False,
            f"⚠️ Plan has {blocked} blocked steps: {blocked_steps}. Address these before completion.",
        )

    remaining = total - completed
    return False, f"📋 Plan {completed}/{total} complete. {remaining} steps remaining."
