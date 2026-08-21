from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from publishing.github_pages import (
    GitHubPagesPublisher,
    WORKSPACE_KEY,
)


class PublishingWorkspaceTests(unittest.TestCase):
    def test_repository_workspace_is_scoped_to_expected_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publisher = GitHubPagesPublisher(root)

            workspace = publisher._repository_workspace(
                "TheTola/letter-smith-publishing"
            )

            self.assertEqual(
                workspace,
                (
                    root
                    / ".lettersmith-publishing-workspaces"
                    / "TheTola--letter-smith-publishing"
                ).resolve(),
            )

    def test_wrong_configured_origin_uses_repository_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrong = root / ".lettersmith-publishing"
            (wrong / ".git").mkdir(parents=True)
            publisher = GitHubPagesPublisher(root)

            with mock.patch.object(
                publisher,
                "_workspace_has_expected_origin",
                return_value=False,
            ):
                workspace = publisher._select_workspace(
                    {WORKSPACE_KEY: str(wrong)},
                    "TheTola/letter-smith-publishing",
                )

            self.assertNotEqual(workspace, wrong)
            self.assertEqual(
                workspace.name,
                "TheTola--letter-smith-publishing",
            )

    def test_matching_configured_workspace_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configured = root / "configured"
            (configured / ".git").mkdir(parents=True)
            publisher = GitHubPagesPublisher(root)

            with mock.patch.object(
                publisher,
                "_workspace_has_expected_origin",
                return_value=True,
            ):
                workspace = publisher._select_workspace(
                    {WORKSPACE_KEY: str(configured)},
                    "TheTola/letter-smith-publishing",
                )

            self.assertEqual(workspace, configured.resolve())


if __name__ == "__main__":
    unittest.main()
