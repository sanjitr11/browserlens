"""Unit tests for WorkflowCache (pure filesystem, no browser)."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from browserlens.compiler.cache import WorkflowCache
from browserlens.compiler.types import CompiledWorkflow, ParameterSlot, make_fingerprint


def make_workflow(wf_id="abc123", task="search for cats", domain="example.com") -> CompiledWorkflow:
    return CompiledWorkflow(
        workflow_id=wf_id,
        task_description=task,
        task_fingerprint=make_fingerprint(task),
        site_domain=domain,
        script_path=f"/tmp/{wf_id}.py",
        parameter_slots=[],
        step_count=3,
        compiled_at="2024-01-01T00:00:00",
        source_trace=None,
    )


class TestWorkflowCache:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cache = WorkflowCache(cache_dir=self.tmpdir)
        self.script_source = "# compiled workflow\nasync def step_0(page, **params): pass\n"

    # ------------------------------------------------------------------ save / load

    def test_save_creates_py_file(self):
        wf = make_workflow()
        self.cache.save(wf, self.script_source)
        assert os.path.isfile(os.path.join(self.tmpdir, f"{wf.workflow_id}.py"))

    def test_save_creates_json_file(self):
        wf = make_workflow()
        self.cache.save(wf, self.script_source)
        assert os.path.isfile(os.path.join(self.tmpdir, f"{wf.workflow_id}.json"))

    def test_save_creates_index_entry(self):
        wf = make_workflow()
        self.cache.save(wf, self.script_source)
        index_path = os.path.join(self.tmpdir, "index.json")
        assert os.path.isfile(index_path)
        with open(index_path) as f:
            index = json.load(f)
        assert wf.workflow_id in index

    def test_load_returns_correct_metadata(self):
        wf = make_workflow(task="buy groceries", wf_id="xyz999")
        saved = self.cache.save(wf, self.script_source)
        loaded = self.cache.load("xyz999")
        assert loaded is not None
        assert loaded.workflow_id == "xyz999"
        assert loaded.task_description == "buy groceries"
        assert loaded.step_count == 3

    def test_load_nonexistent_returns_none(self):
        assert self.cache.load("doesnotexist") is None

    def test_source_trace_not_serialized(self):
        wf = make_workflow()
        self.cache.save(wf, self.script_source)
        loaded = self.cache.load(wf.workflow_id)
        assert loaded is not None
        assert loaded.source_trace is None

    # ------------------------------------------------------------------ lookup_by_task

    def test_lookup_by_task_matches_exact(self):
        wf = make_workflow(task="find cheap flights")
        self.cache.save(wf, self.script_source)
        result = self.cache.lookup_by_task("find cheap flights")
        assert result is not None
        assert result.workflow_id == wf.workflow_id

    def test_lookup_by_task_ignores_punctuation(self):
        wf = make_workflow(task="find cheap flights!")
        self.cache.save(wf, self.script_source)
        result = self.cache.lookup_by_task("Find Cheap Flights")
        assert result is not None
        assert result.workflow_id == wf.workflow_id

    def test_lookup_by_task_ignores_case(self):
        wf = make_workflow(task="Search For Products")
        self.cache.save(wf, self.script_source)
        result = self.cache.lookup_by_task("search for products")
        assert result is not None

    def test_lookup_by_task_with_domain_filter(self):
        wf1 = make_workflow(wf_id="wf1", task="book hotel", domain="hotels.com")
        wf2 = make_workflow(wf_id="wf2", task="book hotel", domain="airbnb.com")
        self.cache.save(wf1, self.script_source)
        self.cache.save(wf2, self.script_source)
        result = self.cache.lookup_by_task("book hotel", site_domain="airbnb.com")
        assert result is not None
        assert result.workflow_id == "wf2"

    def test_lookup_by_task_no_match_returns_none(self):
        assert self.cache.lookup_by_task("something nobody saved") is None

    # ------------------------------------------------------------------ delete

    def test_delete_removes_files(self):
        wf = make_workflow(wf_id="del_me")
        self.cache.save(wf, self.script_source)
        deleted = self.cache.delete("del_me")
        assert deleted is True
        assert not os.path.isfile(os.path.join(self.tmpdir, "del_me.py"))
        assert not os.path.isfile(os.path.join(self.tmpdir, "del_me.json"))

    def test_delete_removes_index_entry(self):
        wf = make_workflow(wf_id="del_me2")
        self.cache.save(wf, self.script_source)
        self.cache.delete("del_me2")
        index_path = os.path.join(self.tmpdir, "index.json")
        with open(index_path) as f:
            index = json.load(f)
        assert "del_me2" not in index

    def test_delete_nonexistent_returns_false(self):
        assert self.cache.delete("ghost_id") is False

    # ------------------------------------------------------------------ corrupted index

    def test_corrupted_index_returns_none_gracefully(self):
        index_path = os.path.join(self.tmpdir, "index.json")
        with open(index_path, "w") as f:
            f.write("{invalid json")
        result = self.cache.lookup_by_task("anything")
        assert result is None

    def test_corrupted_index_load_returns_empty(self):
        index_path = os.path.join(self.tmpdir, "index.json")
        with open(index_path, "w") as f:
            f.write("not json at all")
        # Should not raise
        workflows = self.cache.list_workflows()
        assert workflows == []

    # ------------------------------------------------------------------ list_workflows

    def test_list_workflows_returns_all(self):
        wf1 = make_workflow(wf_id="w1", task="task one")
        wf2 = make_workflow(wf_id="w2", task="task two")
        self.cache.save(wf1, self.script_source)
        self.cache.save(wf2, self.script_source)
        listing = self.cache.list_workflows()
        ids = {e["workflow_id"] for e in listing}
        assert {"w1", "w2"} <= ids

    # ------------------------------------------------------------------ export

    def test_export_copies_script(self):
        wf = make_workflow(wf_id="exportme")
        self.cache.save(wf, self.script_source)
        dest = os.path.join(self.tmpdir, "exported_script.py")
        result_path = self.cache.export("exportme", dest)
        assert os.path.isfile(result_path)
        with open(result_path) as f:
            content = f.read()
        assert "compiled workflow" in content

    def test_export_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            self.cache.export("no_such_wf", "/tmp/out.py")
