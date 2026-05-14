from __future__ import annotations

import unittest


class OrxIntegrationTests(unittest.TestCase):
    def test_repo_exports_integrated_orx_sdk(self) -> None:
        from orx import Orchestrator, TaskRequest

        self.assertIsNotNone(Orchestrator)
        self.assertIsNotNone(TaskRequest)


if __name__ == "__main__":
    unittest.main()
