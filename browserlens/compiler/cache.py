"""Layer 3 — Workflow cache: filesystem store for compiled workflows."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from browserlens.compiler.types import CompiledWorkflow, ParameterSlot, make_fingerprint

_DEFAULT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".browserlens_cache")


class WorkflowCache:
    """
    Filesystem cache for compiled Playwright workflow scripts.

    Directory layout::

        {cache_dir}/
            index.json            # workflow registry
            {wf_id}.py            # compiled Playwright script
            {wf_id}.json          # CompiledWorkflow metadata (no source_trace)
    """

    def __init__(self, cache_dir: str | None = None) -> None:
        self._dir = Path(cache_dir or _DEFAULT_CACHE_DIR)
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _index_path(self) -> Path:
        return self._dir / "index.json"

    def _load_index(self) -> dict:
        try:
            with open(self._index_path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_index(self, index: dict) -> None:
        with open(self._index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)

    def _metadata_path(self, workflow_id: str) -> Path:
        return self._dir / f"{workflow_id}.json"

    def _script_path(self, workflow_id: str) -> Path:
        return self._dir / f"{workflow_id}.py"

    @staticmethod
    def _workflow_to_dict(wf: CompiledWorkflow) -> dict:
        return {
            "workflow_id": wf.workflow_id,
            "task_description": wf.task_description,
            "task_fingerprint": wf.task_fingerprint,
            "site_domain": wf.site_domain,
            "script_path": wf.script_path,
            "step_count": wf.step_count,
            "compiled_at": wf.compiled_at,
            "parameter_slots": [
                {
                    "name": s.name,
                    "step_indices": s.step_indices,
                    "default_value": s.default_value,
                }
                for s in wf.parameter_slots
            ],
            # source_trace intentionally excluded
        }

    @staticmethod
    def _dict_to_workflow(d: dict) -> CompiledWorkflow:
        slots = [
            ParameterSlot(
                name=s["name"],
                step_indices=s.get("step_indices", []),
                default_value=s.get("default_value"),
            )
            for s in d.get("parameter_slots", [])
        ]
        return CompiledWorkflow(
            workflow_id=d["workflow_id"],
            task_description=d["task_description"],
            task_fingerprint=d["task_fingerprint"],
            site_domain=d["site_domain"],
            script_path=d["script_path"],
            parameter_slots=slots,
            step_count=d.get("step_count", 0),
            compiled_at=d.get("compiled_at", ""),
            source_trace=None,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, metadata: CompiledWorkflow, script_source: str) -> CompiledWorkflow:
        """
        Persist a compiled workflow to disk.

        Copies the script to the cache directory (if not already there),
        writes metadata JSON, and updates the index.
        Returns an updated CompiledWorkflow with the canonical script_path.
        """
        wf_id = metadata.workflow_id
        dest_script = self._script_path(wf_id)

        # Write script
        dest_script.write_text(script_source, encoding="utf-8")

        # Update metadata to point at canonical location
        updated = CompiledWorkflow(
            workflow_id=metadata.workflow_id,
            task_description=metadata.task_description,
            task_fingerprint=metadata.task_fingerprint,
            site_domain=metadata.site_domain,
            script_path=str(dest_script),
            parameter_slots=metadata.parameter_slots,
            step_count=metadata.step_count,
            compiled_at=metadata.compiled_at,
            source_trace=metadata.source_trace,
        )

        # Write metadata JSON (without source_trace)
        meta_dict = self._workflow_to_dict(updated)
        self._metadata_path(wf_id).write_text(
            json.dumps(meta_dict, indent=2), encoding="utf-8"
        )

        # Update index
        index = self._load_index()
        index[wf_id] = {
            "site_domain": updated.site_domain,
            "task_fingerprint": updated.task_fingerprint,
            "task_description": updated.task_description,
            "script_path": str(dest_script),
            "metadata_path": str(self._metadata_path(wf_id)),
            "compiled_at": updated.compiled_at,
        }
        self._save_index(index)

        return updated

    def lookup_by_task(
        self, task_description: str, site_domain: str | None = None
    ) -> CompiledWorkflow | None:
        """
        Find a workflow by task description (fuzzy via fingerprint).

        Optionally filters by site_domain.
        """
        target_fp = make_fingerprint(task_description)
        index = self._load_index()
        for wf_id, entry in index.items():
            if entry.get("task_fingerprint") != target_fp:
                continue
            if site_domain is not None and entry.get("site_domain") != site_domain:
                continue
            return self.load(wf_id)
        return None

    def load(self, workflow_id: str) -> CompiledWorkflow | None:
        """Load a CompiledWorkflow from disk by ID. Returns None if not found."""
        meta_path = self._metadata_path(workflow_id)
        if not meta_path.exists():
            return None
        try:
            d = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return self._dict_to_workflow(d)

    def delete(self, workflow_id: str) -> bool:
        """Delete a workflow from the cache. Returns True if it existed."""
        index = self._load_index()
        if workflow_id not in index:
            return False

        # Remove files
        for path in (self._script_path(workflow_id), self._metadata_path(workflow_id)):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

        del index[workflow_id]
        self._save_index(index)
        return True

    def export(self, workflow_id: str, dest_path: str) -> str:
        """
        Copy the compiled script to dest_path.

        Returns the absolute destination path.
        """
        src = self._script_path(workflow_id)
        if not src.exists():
            raise FileNotFoundError(f"Workflow {workflow_id!r} not found in cache")
        shutil.copy2(src, dest_path)
        return str(os.path.abspath(dest_path))

    def list_workflows(self) -> list[dict]:
        """Return a list of index entries for all cached workflows."""
        index = self._load_index()
        return [
            {"workflow_id": wf_id, **entry} for wf_id, entry in index.items()
        ]
