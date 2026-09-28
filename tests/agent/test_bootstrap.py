"""bootstrap 测试：vendor node 下载校验（SHA256）、pi_runtime 解析、verify。"""

import tarfile
from pathlib import Path

import pytest

from agent import bootstrap


NODE_DIRNAME = f"node-{bootstrap.NODE_VERSION}-linux-arm64"


def _make_tarball(tmp_path: Path, name: str) -> Path:
    """构造包含 vendor node 目录结构的 tar.gz。"""
    payload = tmp_path / "payload"
    bin_dir = payload / NODE_DIRNAME / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "node").write_text("#!/bin/sh\necho node\n")
    tarball = tmp_path / f"{name}.tar.gz"
    with tarfile.open(tarball, "w:gz") as tf:
        tf.add(bin_dir / "node", arcname=f"{NODE_DIRNAME}/bin/node")
    return tarball


@pytest.fixture()
def vendor(tmp_path: Path, monkeypatch) -> Path:
    vdir = tmp_path / "vendor"
    vdir.mkdir()
    monkeypatch.setattr(bootstrap, "VENDOR_DIR", vdir)
    monkeypatch.setattr(bootstrap, "REPO_ROOT", tmp_path)
    return vdir


class TestEnsureNode:
    def test_skips_download_when_present(self, vendor: Path, monkeypatch):
        node = bootstrap.node_bin()
        node.parent.mkdir(parents=True)
        node.write_text("existing")
        called = {"n": 0}

        def fake_retrieve(*a, **k):
            called["n"] += 1

        monkeypatch.setattr(bootstrap.urllib.request, "urlretrieve", fake_retrieve)
        assert bootstrap.ensure_node() == node
        assert called["n"] == 0

    def test_download_with_correct_sha_extracts(self, vendor: Path, tmp_path: Path,
                                                monkeypatch):
        tarball = _make_tarball(tmp_path, "node")
        import hashlib
        digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
        monkeypatch.setitem(bootstrap.NODE_SHA256, "arm64", digest)
        monkeypatch.setattr(bootstrap.urllib.request, "urlretrieve",
                            lambda url, dest: shutil_copy(tarball, dest))
        node = bootstrap.ensure_node()
        assert node.is_file()
        assert not list(vendor.glob("*.tar.gz"))  # 下载物清理

    def test_sha_mismatch_aborts_and_cleans(self, vendor: Path, tmp_path: Path,
                                            monkeypatch):
        tarball = _make_tarball(tmp_path, "node")
        monkeypatch.setattr(bootstrap.urllib.request, "urlretrieve",
                            lambda url, dest: shutil_copy(tarball, dest))
        with pytest.raises(SystemExit, match="SHA256"):
            bootstrap.ensure_node()
        assert not list(vendor.glob("*.tar.gz"))  # 坏包已删除


def shutil_copy(src: Path, dest: Path) -> None:
    import shutil
    shutil.copy(src, dest)


class TestPiRuntime:
    def test_missing_runtime_exits_with_hint(self, vendor: Path):
        with pytest.raises(SystemExit, match="ok setup"):
            bootstrap.pi_runtime()

    def test_present_runtime_returns_paths(self, vendor: Path):
        bootstrap.node_bin().parent.mkdir(parents=True)
        bootstrap.node_bin().write_text("n")
        bootstrap.pi_entry().parent.mkdir(parents=True)
        bootstrap.pi_entry().write_text("e")
        node, entry = bootstrap.pi_runtime()
        assert node == str(bootstrap.node_bin())
        assert entry == str(bootstrap.pi_entry())


class TestVerify:
    def test_version_mismatch_exits(self, vendor: Path, monkeypatch):
        bootstrap.pi_entry().parent.mkdir(parents=True)
        bootstrap.pi_entry().write_text("")
        node = bootstrap.node_bin()
        node.parent.mkdir(parents=True)
        node.write_text("")

        class R:
            stdout = "0.99.0\n"

        monkeypatch.setattr(bootstrap.subprocess, "run", lambda *a, **k: R())
        with pytest.raises(SystemExit, match="版本不符"):
            bootstrap.verify(node)

    def test_version_match_passes(self, vendor: Path, monkeypatch, capsys):
        bootstrap.pi_entry().parent.mkdir(parents=True)
        bootstrap.pi_entry().write_text("")
        node = bootstrap.node_bin()
        node.parent.mkdir(parents=True)
        node.write_text("")

        class R:
            stdout = bootstrap.PI_VERSION + "\n"

        monkeypatch.setattr(bootstrap.subprocess, "run", lambda *a, **k: R())
        bootstrap.verify(node)
        assert "校验通过" in capsys.readouterr().out


class TestArch:
    def test_arm64(self, monkeypatch):
        monkeypatch.setattr(bootstrap.platform, "machine", lambda: "aarch64")
        assert bootstrap._arch() == "arm64"

    def test_x64(self, monkeypatch):
        monkeypatch.setattr(bootstrap.platform, "machine", lambda: "x86_64")
        assert bootstrap._arch() == "x64"

    def test_unknown_arch_rejected(self, monkeypatch):
        monkeypatch.setattr(bootstrap.platform, "machine", lambda: "sparc")
        with pytest.raises(SystemExit, match="架构"):
            bootstrap._arch()
