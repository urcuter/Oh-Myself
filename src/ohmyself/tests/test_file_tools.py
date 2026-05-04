from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

from ohmyself.tools.base import ToolExecutionContext
from ohmyself.tools.file_edit_tool import FileEditTool, FileEditToolInput
from ohmyself.tools.file_write_tool import FileWriteTool, FileWriteToolInput
from ohmyself.tools.path_utils import resolve_workspace_path


class WorkspacePathTests(unittest.TestCase):
    def test_resolve_workspace_path_rejects_outside_target(self) -> None:
        workspace = Path.cwd() / "workspace"
        outside = workspace.parent / "outside.txt"
        with self.assertRaisesRegex(ValueError, "outside the workspace"):
            resolve_workspace_path(workspace, str(outside))

    def test_resolve_workspace_path_accepts_inside_target(self) -> None:
        workspace = Path.cwd() / "workspace"
        inside = resolve_workspace_path(workspace, "nested/note.txt")
        self.assertEqual(inside, workspace.resolve() / "nested" / "note.txt")


class WorkspaceBoundaryToolTests(unittest.TestCase):
    def test_write_file_rejects_paths_outside_workspace(self) -> None:
        with patch(
            "ohmyself.tools.file_write_tool.resolve_workspace_path",
            side_effect=ValueError("Path is outside the workspace: C:\\outside.txt"),
        ):
            result = asyncio.run(
                FileWriteTool().execute(
                    FileWriteToolInput(path=r"C:\outside.txt", content="hello"),
                    ToolExecutionContext(cwd=Path.cwd()),
                )
            )
        self.assertTrue(result.is_error)
        self.assertIn("outside the workspace", result.output)

    def test_edit_file_rejects_paths_outside_workspace(self) -> None:
        with patch(
            "ohmyself.tools.file_edit_tool.resolve_workspace_path",
            side_effect=ValueError("Path is outside the workspace: C:\\outside.txt"),
        ):
            result = asyncio.run(
                FileEditTool().execute(
                    FileEditToolInput(path=r"C:\outside.txt", old_str="hello", new_str="bye"),
                    ToolExecutionContext(cwd=Path.cwd()),
                )
            )
        self.assertTrue(result.is_error)
        self.assertIn("outside the workspace", result.output)

    def test_write_file_allows_paths_inside_workspace(self) -> None:
        target = Path.cwd() / "nested" / "note.txt"
        with (
            patch("ohmyself.tools.file_write_tool.resolve_workspace_path", return_value=target),
            patch("pathlib.Path.mkdir", return_value=None) as mkdir_mock,
            patch("pathlib.Path.write_text", return_value=len("hello")) as write_mock,
        ):
            result = asyncio.run(
                FileWriteTool().execute(
                    FileWriteToolInput(path="nested/note.txt", content="hello"),
                    ToolExecutionContext(cwd=Path.cwd()),
                )
            )
        self.assertFalse(result.is_error)
        mkdir_mock.assert_called_once_with(parents=True, exist_ok=True)
        write_mock.assert_called_once_with("hello", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
