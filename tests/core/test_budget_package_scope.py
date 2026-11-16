"""Execute the ledger from real src/site-packages paths without any AI calls.

Only budget.py is copied into isolated test locations. Main separately verifies
the complete built wheel in its retained QA environment. All journals here are
new fictional fixtures, never the real Main quota ledger.
"""

import importlib.util
from pathlib import Path
import sys
from uuid import uuid4

import pytest

from campusops.agent_core import budget as source_budget
from campusops.agent_core.config import CoreSettings
from campusops.agent_core.errors import CoreFailure


def project(root, *, name="campusops"):
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n', encoding="utf-8")
    (root / "AGENTS.md").write_text("Fictional CampusOps test context\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs/project-spec-v2.md").write_text("Fictional GearShift scope\n", encoding="utf-8")
    return root


def load_budget_at(path, monkeypatch):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(Path(source_budget.__file__).read_bytes())
    name = "campusops.agent_core._scope_test_" + uuid4().hex
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def installed(tmp_path, monkeypatch):
    path = tmp_path / "installed-env/Lib/site-packages/campusops/agent_core/budget.py"
    return load_budget_at(path, monkeypatch)


def test_site_packages_uses_current_project_without_creating_lib_var(installed, tmp_path, monkeypatch):
    root = project(tmp_path / "CampusOps")
    monkeypatch.chdir(root)
    ledger = installed.BudgetLedger()
    assert "site-packages" in installed.__file__
    assert ledger.directory == root / "var/model-budget"
    assert not (root / "var").exists()
    assert not (tmp_path / "installed-env/Lib/var").exists()


def test_editable_and_installed_modes_keep_one_existing_fixture_journal(installed, tmp_path, monkeypatch):
    root = project(tmp_path / "CampusOps")
    editable = load_budget_at(root / "src/campusops/agent_core/budget.py", monkeypatch)
    monkeypatch.chdir(root)
    original = editable.BudgetLedger()
    original.reserve("fictional_prior_run", CoreSettings())
    journal = root / "var/model-budget/usage.jsonl"
    before = journal.read_bytes()
    packaged = installed.BudgetLedger()
    assert packaged.directory == original.directory
    snapshot = packaged.get_model_budget("fictional_prior_run", CoreSettings())
    assert snapshot.status == "AVAILABLE" and snapshot.run_reserved == snapshot.daily_reserved == 1
    assert journal.read_bytes() == before
    assert not (tmp_path / "installed-env/Lib/var").exists()


def test_editable_stays_in_its_source_tree_when_cwd_changes(tmp_path, monkeypatch):
    root = project(tmp_path / "CampusOps")
    editable = load_budget_at(root / "src/campusops/agent_core/budget.py", monkeypatch)
    elsewhere = tmp_path / "unrelated-cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert editable.BudgetLedger().directory == root / "var/model-budget"
    assert not (elsewhere / "var").exists()


def test_installed_mode_requires_project_context_without_parent_search(installed, tmp_path, monkeypatch):
    root = project(tmp_path / "CampusOps")
    nested = root / "unconfigured-subdirectory"
    nested.mkdir()
    monkeypatch.chdir(nested)
    with pytest.raises(CoreFailure, match="BUDGET_PROJECT_CONTEXT_REQUIRED"):
        installed.BudgetLedger()
    assert not (root / "var").exists() and not (nested / "var").exists()


def test_installed_mode_accepts_explicit_absolute_project_root(installed, tmp_path, monkeypatch):
    root = project(tmp_path / "CampusOps")
    elsewhere = tmp_path / "unconfigured-cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert installed.BudgetLedger(project_root=root).directory == root / "var/model-budget"
    with pytest.raises(CoreFailure, match="BUDGET_PROJECT_CONTEXT_REQUIRED"):
        installed.BudgetLedger(project_root=Path("relative-root"))
    assert not (root / "var").exists()


def test_unpacked_installed_context_can_reuse_one_explicit_existing_authority(installed, tmp_path, monkeypatch):
    """Fictional authority/extraction only; never access the Main project ledger."""
    authority = project(tmp_path / "FictionalAuthority")
    extracted = project(tmp_path / "FictionalFreshExtraction")
    editable = load_budget_at(authority / "src/campusops/agent_core/budget.py", monkeypatch)
    limits = CoreSettings(daily_calls=2)
    original = editable.BudgetLedger()
    original.reserve("fictional_old_authorization", limits)
    journal = authority / "var/model-budget/usage.jsonl"
    before = journal.read_bytes()
    monkeypatch.chdir(extracted)
    reused = installed.BudgetLedger(project_root=authority)
    assert reused.directory == original.directory
    assert reused.get_model_budget("fictional_new_authorization", limits).daily_reserved == 1
    assert journal.read_bytes() == before
    reused.reserve("fictional_new_authorization", limits)
    assert journal.read_bytes().startswith(before)
    with pytest.raises(CoreFailure, match="DAILY_MODEL_BUDGET_EXCEEDED"):
        installed.BudgetLedger(project_root=authority).reserve("fictional_third_authorization", limits)
    assert not (extracted / "var").exists()
    with pytest.raises(CoreFailure, match="BUDGET_DIRECTORY_OUTSIDE_WORKTREE"):
        installed.BudgetLedger(extracted / "var/model-budget", project_root=authority)


def test_installed_scope_rejects_directory_outside_selected_project(installed, tmp_path, monkeypatch):
    root = project(tmp_path / "CampusOps")
    monkeypatch.chdir(root)
    with pytest.raises(CoreFailure, match="BUDGET_DIRECTORY_OUTSIDE_WORKTREE"):
        installed.BudgetLedger(tmp_path / "different-ledger")
    assert not (tmp_path / "different-ledger").exists()


@pytest.mark.parametrize("missing", ["AGENTS.md", "docs/project-spec-v2.md"])
def test_installed_root_requires_campusops_markers(installed, tmp_path, monkeypatch, missing):
    root = tmp_path / "incomplete-context"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname="campusops"\n', encoding="utf-8")
    if missing != "AGENTS.md":
        (root / "AGENTS.md").write_text("Fictional context\n", encoding="utf-8")
    if missing != "docs/project-spec-v2.md":
        (root / "docs").mkdir()
        (root / "docs/project-spec-v2.md").write_text("Fictional spec\n", encoding="utf-8")
    monkeypatch.chdir(root)
    with pytest.raises(CoreFailure, match="BUDGET_PROJECT_CONTEXT_REQUIRED"):
        installed.BudgetLedger()
    assert not (root / "var").exists()


def test_wrong_project_identity_is_rejected_without_creating_budget(installed, tmp_path, monkeypatch):
    root = project(tmp_path / "wrong-identity", name="fictional-unrelated-project")
    monkeypatch.chdir(root)
    with pytest.raises(CoreFailure, match="BUDGET_PROJECT_CONTEXT_REQUIRED"):
        installed.BudgetLedger()
    assert not (root / "var").exists()


def test_editable_cannot_be_redirected_to_a_different_project(tmp_path, monkeypatch):
    root = project(tmp_path / "CampusOps")
    other = project(tmp_path / "second-fixture-project")
    editable = load_budget_at(root / "src/campusops/agent_core/budget.py", monkeypatch)
    with pytest.raises(CoreFailure, match="BUDGET_PROJECT_ROOT_MISMATCH"):
        editable.BudgetLedger(project_root=other)
    assert not (root / "var").exists() and not (other / "var").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows D-drive project isolation")
def test_non_d_project_context_is_rejected_before_filesystem_resolution(installed, monkeypatch):
    resolve = Path.resolve
    def reject_c_access(path, *args, **kwargs):
        assert path.drive.casefold() != "c:", "C-drive context must not be resolved/read"
        return resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", reject_c_access)
    with pytest.raises(CoreFailure, match="BUDGET_PROJECT_CONTEXT_REQUIRED"):
        installed.BudgetLedger(project_root=Path("C:/CampusOps"))
