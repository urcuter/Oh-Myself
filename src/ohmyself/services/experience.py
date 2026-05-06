from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ohmyself.config.paths import get_home_dir

DEFAULT_EXPERIENCE_FILENAME = "default.md"


@dataclass(frozen=True)
class ExperienceEntry:
    entry_id: str
    path: Path
    content: str
    created_at: datetime


def get_experience_dir() -> Path:
    path = get_home_dir() / "experiences"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_default_experience_path() -> Path:
    return get_experience_dir() / DEFAULT_EXPERIENCE_FILENAME


def ensure_experience_library() -> Path:
    path = get_default_experience_path()
    if not path.exists():
        path.write_text(
            "# Default Experience Library\n\n"
            "New experience entries are appended here first. Use `/exper organize` to let AI classify them into topic files.\n",
            encoding="utf-8",
        )
    return path


def append_experience(content: str, *, now: datetime | None = None) -> ExperienceEntry:
    cleaned = content.strip()
    if not cleaned:
        raise ValueError("experience content cannot be empty")
    created_at = now or datetime.now().astimezone()
    entry_id = f"EXP-{created_at.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
    path = ensure_experience_library()
    block = "\n\n".join(
        [
            f"## {entry_id}",
            f"- created_at: {created_at.isoformat(timespec='seconds')}",
            "- source: /exper add",
            "- content:",
            _indent_content(cleaned),
        ]
    )
    existing = path.read_text(encoding="utf-8", errors="replace")
    separator = "\n\n" if existing.strip() else ""
    path.write_text(f"{existing.rstrip()}{separator}{block}\n", encoding="utf-8")
    return ExperienceEntry(entry_id=entry_id, path=path, content=cleaned, created_at=created_at)


def has_experience_content() -> bool:
    experience_dir = get_experience_dir()
    for path in experience_dir.glob("*.md"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if "## EXP-" in text:
            return True
    return False


def build_experience_retrieval_task(question: str) -> str:
    cleaned = question.strip()
    if not cleaned:
        raise ValueError("experience question cannot be empty")
    experience_dir = get_experience_dir()
    return f"""\
你是 experience_retriever，只负责检索本地生活经验库，不负责给用户最终建议。

用户问题：
{cleaned}

经验库目录：
{experience_dir}

请执行：
1. 使用 glob/read_file/grep 阅读 `{experience_dir}` 下的 markdown 经验库。
2. 找出和用户问题相关的经验片段。
3. 返回来源文件、经验 ID 或标题、相关原文摘录、相关原因、相关度。
4. 如果没有相关经验，明确说没有找到足够依据。
5. 不要回答用户应该怎么做，只返回检索报告。
"""


def build_experience_answer_prompt(question: str, retrieval_report: str) -> str:
    cleaned_question = question.strip()
    cleaned_report = retrieval_report.strip() or "(experience_retriever did not return any content)"
    if not cleaned_question:
        raise ValueError("experience question cannot be empty")
    return f"""\
用户通过 `/exper` 提问。experience_retriever subagent 已经读取本地生活经验库，并返回了检索报告。

用户问题：
{cleaned_question}

experience_retriever 检索报告：
{cleaned_report}

请基于检索报告回答用户：
1. 回答里要区分“经验库证据”和“你的推断/建议”。
2. 如果检索报告显示经验库证据不足，直接说明不足，不要把泛泛建议伪装成用户过往经验。
3. 不要再次调用 subagent 或重新检索经验库。
"""


def build_experience_organize_prompt() -> str:
    experience_dir = get_experience_dir()
    default_path = ensure_experience_library()
    return f"""\
用户请求整理生活经验库。

经验库目录：
{experience_dir}

默认经验库：
{default_path}

请执行：
1. 阅读 `{default_path}` 和 `{experience_dir}` 下已有的分类 markdown 文件。
2. 将 default.md 中尚未分类或适合归档的经验，整理到若干分类 markdown 文件中，例如 work.md、learning.md、health.md、relationships.md，也可以根据内容创建更合适的英文文件名。
3. 分类文件顶部保留简短 summary，说明该文件覆盖的经验范围。
4. 每条经验保留原始经验 ID、created_at 和 content，不要改写成空泛总结。
5. 初版要求非破坏性整理：不要删除 default.md 中的原始条目。可以在最终回复中说明哪些条目被复制到了哪些分类文件。
6. 如需写文件，使用现有文件工具完成。
"""


def _indent_content(content: str) -> str:
    return "\n".join(f"  {line}" if line else "  " for line in content.splitlines())
