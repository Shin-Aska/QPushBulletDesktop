"""Build/runtime manifest contract and candidate publication boundaries; no builds."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location('pbdesk_' + name, ROOT / 'packaging' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load('build_appimage')
guard = load('release_guard')


def test_manifest_is_accepted_by_production_bootstrap_and_seals_every_byte(tmp_path):
    from pbdesk.packaged import verify_runtime, PackagedRuntimeError
    root = tmp_path / 'runtime'
    (root / '_internal/unused/empty').mkdir(parents=True)
    (root / 'pbdesk').write_bytes(b'fictional frozen executable')
    (root / 'pbdesk').chmod(0o775)
    (root / '_internal/library.so').write_bytes(b'fictional library')
    (root / '_internal/link.so').symlink_to('library.so')
    identity = builder.runtime_manifest(root)
    raw = (root / builder.MANIFEST).read_bytes()
    assert identity == hashlib.sha256(raw).hexdigest()
    assert raw.endswith(b'\n')
    assert (root / builder.IDENTITY).read_text() == identity + '\n'
    assert not (root / '_internal/unused').exists()
    assert verify_runtime(root) == identity
    assert json.loads(raw)['files']['pbdesk']['mode'] == 0o755
    (root / '_internal/library.so').write_bytes(b'tampered library')
    with pytest.raises(PackagedRuntimeError, match='verification failed'):
        verify_runtime(root)


@pytest.mark.parametrize('target', ['../../outside', '/tmp/outside', 'missing.so'])
def test_builder_rejects_links_outside_or_missing_from_runtime(tmp_path, target):
    root = tmp_path / 'runtime'
    root.mkdir()
    (root / 'pbdesk').write_text('executable')
    (root / 'pbdesk').chmod(0o755)
    (root / 'link').symlink_to(target)
    with pytest.raises(ValueError, match='symlink'):
        builder.runtime_manifest(root)


def test_matching_source_archive_captures_working_tree_bytes_and_excludes_profile(tmp_path, monkeypatch):
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src/current.py').write_text('# changed after HEAD\n')
    (tmp_path / 'requirements-build.lock').write_text('# pinned build inputs\n')
    private = ['.runtime/credentials.json', '.venv/bin/python', '.env', 'pbdesk.sqlite3',
               'service-credentials.json', 'pbdesk-recovery-key.txt', '.pbdesk-history.lock',
               'private.db', 'private.db-wal', 'private.sqlite']
    names = ['src/current.py', 'requirements-build.lock'] + private
    monkeypatch.setattr(builder, 'run', lambda *a, **k: SimpleNamespace(stdout=b'\0'.join(n.encode() for n in names)))
    archive = tmp_path / 'source.tar.gz'
    manifest = builder.source_archive(tmp_path, archive, epoch=123)
    assert set(manifest) == {'src/current.py', 'requirements-build.lock'}
    with tarfile.open(archive) as source:
        assert source.extractfile('pbdesk-source/src/current.py').read() == b'# changed after HEAD\n'
        assert source.getmember('pbdesk-source/src/current.py').mtime == 123
    assert manifest['src/current.py']['sha256'] == hashlib.sha256(b'# changed after HEAD\n').hexdigest()


def readiness_root(tmp_path, version='1.0.0', **overrides):
    (tmp_path / 'packaging').mkdir()
    (tmp_path / 'pyproject.toml').write_text('[project]\nversion = "' + version + '"\n')
    value = dict(final_release_ready=True, corresponding_source_complete=True,
                 supported_desktop_acceptance_complete=True, encrypted_profile_upgrade_verified=True,
                 blockers=[])
    value.update(overrides)
    (tmp_path / 'packaging/release-readiness.json').write_text(json.dumps(value))
    return tmp_path


def test_private_repo_cannot_become_a_final_release(tmp_path):
    with pytest.raises(ValueError, match='public repository'):
        guard.validate(readiness_root(tmp_path), 'v1.0.0', private=True)


@pytest.mark.parametrize('version,tag', [('0.1.0', 'v0.1.0'), ('1.0.0', 'v1.0.1'), ('1.0.0rc1', 'v1.0.0rc1')])
def test_final_tag_requires_exact_stable_public_version(tmp_path, version, tag):
    with pytest.raises(ValueError, match='stable project version'):
        guard.validate(readiness_root(tmp_path, version), tag, private=False)


def test_final_release_fails_closed_on_source_accounting_or_acceptance_gap(tmp_path):
    root = readiness_root(tmp_path, corresponding_source_complete=False)
    with pytest.raises(ValueError, match='blocked'):
        guard.validate(root, 'v1.0.0', private=False)


def test_build_is_refused_outside_temporary_actions_runner(monkeypatch):
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    with pytest.raises(SystemExit) as exc:
        builder.main([])
    assert exc.value.code == 2


def test_mcp_collection_filters_optional_cli_before_importing_packages():
    # Inspect the actual spec without importing or installing the CI-only builder.
    # Filtering the mcp.cli package itself is necessary: filtering its children
    # alone would still import __init__, which exits when typer is absent.
    import ast
    tree = ast.parse((ROOT / 'packaging/pbdesk-appimage.spec').read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    collect = next(node for node in calls if isinstance(node.func, ast.Name)
                   and node.func.id == 'collect_submodules' and node.args
                   and isinstance(node.args[0], ast.Constant) and node.args[0].value == 'mcp')
    predicate_ast = next(item.value for item in collect.keywords if item.arg == 'filter')
    predicate = eval(compile(ast.Expression(predicate_ast), '<spec-filter>', 'eval'))
    for name in ['mcp.cli', 'mcp.cli.cli', 'mcp.cli.__main__', 'mcp.__main__']:
        assert not predicate(name), name
    for name in ['mcp', 'mcp.server', 'mcp.server.mcpserver', 'mcp.server.stdio',
                 'mcp.client', 'mcp.client.stdio', 'mcp.shared']:
        assert predicate(name), name
    analysis = next(node for node in calls if isinstance(node.func, ast.Name) and node.func.id == 'Analysis')
    excluded = ast.literal_eval(next(item.value for item in analysis.keywords if item.arg == 'excludes'))
    assert {'mcp.cli', 'mcp.__main__'} <= set(excluded)
