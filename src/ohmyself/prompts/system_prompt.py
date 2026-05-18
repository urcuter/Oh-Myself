from __future__ import annotations

from ohmyself.config.paths import get_memory_dir
from ohmyself.prompts.environment import EnvironmentInfo, get_environment_info

_FOUNDATION_SYSTEM_PROMPT = """\
你是 [user] 的长期成长助手，负责在学习、计划、执行、复盘和日常管理中提供持续支持。

你的核心目标是帮助 [user] 更清楚地思考、更稳定地行动，并逐步形成独立判断与自我管理能力。你提供支持，但不替代 [user] 做人生选择；你可以提出判断和异议，但最终决定权始终属于 [user]。

你的工作重点包括：
- 帮助 [user] 拆解目标，制定现实可行的计划
- 根据当前日期和上下文跟进进展、记录变化、辅助复盘
- 在遇到偏差、拖延或困难时，先识别原因，再调整路径
- 在需要时提供建议、框架、提醒、总结和下一步行动
- 引导 [user] 改进思考方式，逐步学会抓住问题本质，而不是停留在表面现象、直觉反应或即时情绪上
- 帮助 [user] 从多个角度看问题，包括目标、约束、长期影响、他人视角和系统关系
- 鼓励 [user] 进行反向思考，检验自己的假设、判断和直觉，识别可能的盲点

你的协作原则：
- 保持清晰、直接、诚实
- 当 [user] 的短期偏好与长期目标冲突时，明确指出问题
- 不一味顺从，也不空泛说教
- 优先帮助 [user] 看清问题、权衡选项、推进行动
- 目标是提升 [user] 的独立思考能力，而不是让 [user] 依赖你

你的默认回应方式：
- 先响应当前请求，再补充必要说明
- 默认简洁、自然、克制
- 优先给出可执行的信息、建议或下一步
- 在 CLI 场景中，清晰、简洁、可控优先于表达欲
- 不主动进行长篇自我介绍、情绪渲染或关系铺垫

关于提问与引导：
- 不要把每个问题都当作思考训练题
- 当 [user] 的请求是信息获取、事实判断、具体执行或明确求解时，直接回答
- 当问题涉及选择、复盘、长期规划、认知偏差、反复受阻或明显被情绪主导时，可以通过追问、框架或反向思考帮助 [user] 看清问题本质
- 提问应服务于推进，不要用提问代替回答
- 可以提供思考框架，但不要让对话变得沉重、拖沓或过度教育化

当 [user] 明显被情绪带着走时，你可以先承接其表达，再帮助其回到事实、目标、约束、选择和行动上。

关于受保护的用户文件：
- coping.md 和 strategy.md 位于数据目录（~/.ohmyself/），是 [user] 个人维护的参考文件。
- 你可以读取这些文件来提供建议，但不得在未获得 [user] 明确指示的情况下修改或创建它们。
- 如果 [user] 明确要求你更新这些文件，你才可以使用 write_file 或 edit_file 工具操作，且系统会二次确认。

关于长期策略（strategy.md）的讨论与修改：
- 长期策略是 [user] 未来发展的方向性指引，所有目标设定和计划制定都应依据该策略。
- 当 [user] 说"我想修改长期策略"或表达类似意图时，你应进入协作讨论模式：
  1. 先读取当前 strategy.md 的内容，呈现给 [user]
  2. 了解 [user] 想要修改的方向和原因
  3. 帮助 [user] 按以下三条准则审视策略内容：
     a. **未来趋势性**：该策略是否代表了未来长期的发展趋势？是否经得起时间考验？反思它真的能够代表未来发展趋势吗？
     b. **基础性**：该策略是否聚焦于基础性能力或方向（如编程、语言、思维框架等）？基础性的东西具有长期复利价值，不会因短期潮流变化而失效。
     c. **可实现性**：该策略是否可行？实施过程中可能遇到哪些困难？有哪些解决方法？需要什么资源或条件？
  4. 讨论达成共识后，由你整理出更新后的完整策略文本，经 [user] 确认后写入 strategy.md
- 在 [user] 明确同意前，不得自行修改 strategy.md。写入操作会触发系统二次确认。

关于长期日程计划（long_plan.json）：
- 长期日程计划是将长期策略和活跃目标转化为基于日历的分阶段执行计划。
- 计划存储在数据目录的 long_plan.json 文件中，包含阶段（Phase）、里程碑（Milestone）和执行记录。
- 你会在系统提示中看到「你的长期日程计划」section（如果用户已启用），其中包含当前阶段、近期里程碑和节奏概况。
- 你可以读取 long_plan.json 来了解完整计划，但修改计划需要通过 /longplan 命令或用户明确指示。
- 在每日计划生成和审视时，应主动参考长期计划中的里程碑，将本周应推进的事项纳入每日计划。
- 当发现用户进度持续超前或滞后时，可建议用户使用 /longplan review 或 /longplan adapt 进行节奏调整。
- 当用户表示想"追踪今日进展"或"审视长期计划"时，引导其使用对应的 /longplan 命令。
"""

_MODEL_PROFILE_FILENAME = "model_profile.md"
_USER_PROFILE_FILENAME = "user_profile.md"


def _format_environment_section(env: EnvironmentInfo) -> str:
    lines = [
        "# Environment",
        f"- OS: {env.os_name} {env.os_version}",
        f"- Architecture: {env.platform_machine}",
        f"- Shell: {env.shell}",
        f"- Working directory: {env.cwd}",
        f"- Date: {env.date}",
        f"- Python: {env.python_version}",
        f"- Python executable: {env.python_executable}",
    ]
    if env.virtual_env:
        lines.append(f"- Virtual environment: {env.virtual_env}")
    if env.is_git_repo:
        git_line = "- Git: yes"
        if env.git_branch:
            git_line += f" (branch: {env.git_branch})"
        lines.append(git_line)
    if env.os_name == "Windows":
        lines.append("")
        lines.append(
            "# IMPORTANT: On Windows, the bash tool runs PowerShell. "
            "PowerShell commands (Set-Content, Out-File, Add-Content) default to UTF-16LE encoding. "
            "Always use the write_file tool (UTF-8) instead of bash to create or edit text files.",
        )
    return "\n".join(lines)


def _load_model_profile_memory() -> str:
    path = _ensure_memory_file(_MODEL_PROFILE_FILENAME)
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _load_user_profile_memory() -> str:
    path = get_memory_dir() / _USER_PROFILE_FILENAME
    if not path.exists() or path.is_dir():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _ensure_memory_file(filename: str):
    path = get_memory_dir() / filename
    if path.exists():
        return path
    try:
        path.write_text("", encoding="utf-8")
    except OSError:
        pass
    return path


def _build_layered_prompt(user_prompt: str | None) -> str:
    sections = [
        "# Layer 1: Base Prompt",
        _FOUNDATION_SYSTEM_PROMPT.strip(),
    ]
    cleaned_user_prompt = (user_prompt or "").strip()
    if cleaned_user_prompt:
        sections.extend(
            [
                "# Layer 2: User Prompt",
                cleaned_user_prompt,
            ]
        )
    model_profile_text = _load_model_profile_memory()
    if model_profile_text:
        sections.extend(
            [
                "# Layer 3: Model Profile",
                model_profile_text,
            ]
        )
    memory_text = _load_user_profile_memory()
    if memory_text:
        sections.extend(
            [
                "# Layer 3 Memory: User Profile",
                memory_text,
            ]
        )
    return "\n\n".join(sections)


def normalize_user_system_prompt(value: str | None) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    if "# Layer 1: Base Prompt" in cleaned:
        layer2_marker = "# Layer 2: User Prompt"
        if layer2_marker not in cleaned:
            return ""
        section = cleaned.split(layer2_marker, 1)[1].strip()
        cut_indexes = [index for index in (section.find("# Layer 3:"), section.find("# Environment")) if index != -1]
        if cut_indexes:
            section = section[: min(cut_indexes)].strip()
        return section
    foundation = _FOUNDATION_SYSTEM_PROMPT.strip()
    if cleaned.startswith(foundation) and "# Environment" in cleaned:
        return ""
    legacy_foundations = (
        "You are Oh Myself, a standalone terminal AI agent.",
    )
    if any(cleaned.startswith(prefix) for prefix in legacy_foundations) and "# Environment" in cleaned:
        return ""
    return cleaned


def build_system_prompt(
    custom_prompt: str | None = None,
    *,
    env: EnvironmentInfo | None = None,
    cwd: str | None = None,
    goal_context: str | None = None,
    strategy_content: str | None = None,
    long_plan_content: str | None = None,
    workflow_context: str | None = None,
) -> str:
    resolved_env = env or get_environment_info(cwd=cwd)
    layered = _build_layered_prompt(normalize_user_system_prompt(custom_prompt))
    result = f"{layered}\n\n{_format_environment_section(resolved_env)}"
    if strategy_content and strategy_content.strip():
        result = f"{result}\n\n## 你的长期策略\n\n{strategy_content.strip()}"
    if long_plan_content and long_plan_content.strip():
        result = f"{result}\n\n{long_plan_content.strip()}"
    if workflow_context and workflow_context.strip():
        result = f"{result}\n\n{workflow_context.strip()}"
    if goal_context:
        result = f"{result}\n\n{goal_context}"
    return result
