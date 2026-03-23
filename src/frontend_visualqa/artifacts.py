"""Artifact storage for visual QA runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True)
class RunArtifacts:
    """Filesystem locations for a single run."""

    run_id: str
    run_dir: Path


class ArtifactManager:
    """Create run-scoped directories and persist evidence files."""

    def __init__(self, base_dir: str | Path = "artifacts") -> None:
        self.base_dir = Path(base_dir)

    def create_run(self, prefix: str = "run", run_id: str | None = None) -> RunArtifacts:
        """Create a directory for a new run and return its metadata."""

        resolved_run_id = run_id or self._build_run_id(prefix)
        run_dir = self.base_dir / resolved_run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return RunArtifacts(run_id=resolved_run_id, run_dir=run_dir)

    def claim_dir(self, run: RunArtifacts, claim_index: int) -> Path:
        """Create and return the directory for a claim within a run."""

        claim_dir = run.run_dir / f"claim-{claim_index:02d}"
        claim_dir.mkdir(parents=True, exist_ok=True)
        return claim_dir

    def save_screenshot(
        self,
        run: RunArtifacts,
        claim_index: int,
        label: str,
        image_bytes: bytes,
    ) -> str:
        """Persist a screenshot and return its path."""

        path = self.claim_dir(run, claim_index) / f"{label}.png"
        path.write_bytes(image_bytes)
        return str(path)

    def save_trace(self, run: RunArtifacts, claim_index: int, action_trace: list[str]) -> str:
        """Persist the executed action trace and return its path."""

        path = self.claim_dir(run, claim_index) / "action_trace.json"
        path.write_text(json.dumps(action_trace, indent=2))
        return str(path)

    def save_proof_text(self, run: RunArtifacts, claim_index: int, label: str, text: str) -> str:
        """Persist extracted proof text and return its path."""

        path = self.claim_dir(run, claim_index) / f"{label}.txt"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def save_json(self, run: RunArtifacts, relative_path: str, payload: dict) -> str:
        """Persist arbitrary JSON within the run directory."""

        path = run.run_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2))
        return str(path)

    @staticmethod
    def _build_run_id(prefix: str) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return f"{prefix}-{timestamp}-{uuid4().hex[:8]}"
