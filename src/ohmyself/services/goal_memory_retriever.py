from __future__ import annotations

from ohmyself.services.goal_memory import (
    get_goal_memory_dir,
    get_goal_experience_dir,
)
from ohmyself.services.goal_session import get_session_summaries_dir
from ohmyself.services.experience import get_experience_dir


def build_goal_memory_retrieval_task(
    goal_id: str,
    query: str,
    *,
    deep: bool = False,
) -> str:
    memory_dir = str(get_goal_memory_dir(goal_id))
    experience_dir = str(get_goal_experience_dir(goal_id))
    global_experience_dir = str(get_experience_dir())

    dirs_to_search = [memory_dir, experience_dir]
    dir_instructions = [f"  - {memory_dir} (AI 整理的长期记忆：ai_notes.md, user_prefs.md, context.md)"]
    dir_instructions.append(f"  - {experience_dir} (目标专属经验库)")

    if deep:
        summaries_dir = str(get_session_summaries_dir(goal_id))
        dirs_to_search.append(summaries_dir)
        dir_instructions.append(f"  - {summaries_dir} (历史会话摘要)")

    dirs_to_search.append(global_experience_dir)
    dir_instructions.append(f"  - {global_experience_dir} (全局经验库，fallback)")

    dirs_text = "\n".join(dir_instructions)

    return f"""\
你是 goal_memory_retriever，只负责检索当前目标的长期记忆和相关经验，不负责给用户最终建议。

目标 ID: {goal_id}
查询问题: {query}

检索范围（按优先级）：
{dirs_text}

请执行：
1. 按优先级顺序使用 glob/read_file/grep 阅读上述目录下的 markdown 文件。
2. 找出与查询问题相关的内容片段。
3. 返回结构化报告：
   - 来源文件路径
   - 记忆/经验 ID 或标题
   - 相关原文摘录
   - 相关原因（为什么与查询相关）
   - 相关度（高/中/低）
4. 如果没有找到相关内容，明确说没有找到。
5. 不要回答用户应该怎么做，只返回检索报告。
"""
