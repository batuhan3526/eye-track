# gaze/tests/test_downloader.py
"""The model downloader against a local HTTP server (no internet: the Linux
run has --network none). The server speaks Range, and can be told to cut
the connection, ignore Range, hang, or act as a proxy.

What must hold for the user: a file only ever lands complete and verified;
a cut or cancelled download resumes instead of starting over; a failure is
reported in Turkish (console line ASCII) and never leaves a half file where
the program would load it.
"""
import errno
import hashlib
import http.server
import io
import os
import re
import socket
import socketserver
import sys
import threading
import time
import urllib.request
import zipfile

import pytest

from eye_gaze import downloader as dl
from eye_gaze import models_manifest as mm
from eye_gaze import paths


# --------------------------------------------------------------- server ---

class _QuickServer(http.server.ThreadingHTTPServer):
    def server_bind(self):
        # HTTPServer.server_bind does a reverse DNS lookup (getfqdn): about
        # a second per test on Windows, for a name nobody reads.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class _Server:
    """Serves self.files over HTTP/1.0 on 127.0.0.1 with Range support."""

    def __init__(self):
        self.files = {}            # "/name" -> bytes
        self.cut_after = {}        # "/name" -> body bytes sent before a cut
        self.ignore_range = False
        self.hang = False
        self.chunk_delay = 0.0
        self.requests = []         # (path, Range header or None, proxied)
        self.stop = threading.Event()
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                path, proxied = self.path, False
                if path.startswith("http://"):          # proxy request line
                    proxied = True
                    path = "/" + path.split("/", 3)[3]
                rng = self.headers.get("Range")
                owner.requests.append((path, rng, proxied))
                if owner.hang:
                    owner.stop.wait(10)
                    return
                body = owner.files.get(path)
                if body is None:
                    self.send_error(404)
                    return
                start = 0
                m = re.fullmatch(r"bytes=(\d+)-", rng or "")
                if m and not owner.ignore_range:
                    start = int(m.group(1))
                    if start >= len(body):
                        self.send_response(416)
                        self.send_header("Content-Range",
                                         "bytes */%d" % len(body))
                        self.end_headers()
                        return
                    self.send_response(206)
                    self.send_header("Content-Range", "bytes %d-%d/%d" % (
                        start, len(body) - 1, len(body)))
                else:
                    self.send_response(200)
                self.send_header("Content-Length", str(len(body) - start))
                self.end_headers()
                rest = body[start:]
                cut = owner.cut_after.pop(path, None)
                if cut is not None:
                    rest = rest[:cut]
                for i in range(0, len(rest), 8192):
                    if owner.chunk_delay:
                        time.sleep(owner.chunk_delay)
                    try:
                        self.wfile.write(rest[i:i + 8192])
                    except OSError:
                        return
                if cut is not None:
                    self.wfile.flush()
                    self.close_connection = True

        self.httpd = _QuickServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.02},
                                       daemon=True)
        self.thread.start()

    def url(self, name):
        return "http://127.0.0.1:%d/%s" % (self.port, name)

    def close(self):
        self.stop.set()
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = _Server()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """No real proxy (the machine's registry/env would route 127.0.0.1
    through it), fresh module state, fast retries."""
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    monkeypatch.setattr(dl, "last_error", None)
    monkeypatch.setattr(dl, "_ssl_ctx", None)
    monkeypatch.setattr(dl, "PROGRESS_INTERVAL", 0.0)


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _blob(n, seed=1):
    return bytes((i * 31 + seed) & 0xFF for i in range(n))


def _item(server, name, body, kind="file", sha=None, dest_rel=None):
    server.files["/" + name] = body
    return mm.Item(name=name, url=server.url(name), size=len(body),
                   sha256=sha or hashlib.sha256(body).hexdigest(),
                   dest_rel=dest_rel or "test/" + name, kind=kind)


def _root(tmp_path):
    """A download root with Turkish letters on every OS (tmp_path is only
    non-ASCII on this Windows profile)."""
    r = tmp_path / "modeller-ğüşıöç"
    r.mkdir()
    return str(r)


# ------------------------------------------------------- plain download ---

def test_download_lands_verified_file_under_non_ascii_root(server, tmp_path):
    body = _blob(100_000)
    it = _item(server, "a.bin", body)
    root = _root(tmp_path)
    got = dl.download(it, root, None, threading.Event())
    assert got == os.path.join(root, "test", "a.bin")
    assert _read(got) == body
    assert not os.path.exists(got + ".part")


def test_resume_from_partial(server, tmp_path):
    body = _blob(200_000)
    it = _item(server, "r.bin", body)
    cut = len(body) * 40 // 100
    server.cut_after["/r.bin"] = cut
    root = _root(tmp_path)
    dest = os.path.join(root, "test", "r.bin")
    with pytest.raises(dl.DownloadError) as e:
        dl.download(it, root, None, threading.Event())
    assert e.value.code == "kesildi"
    assert os.path.getsize(dest + ".part") == cut
    assert not os.path.exists(dest)

    assert dl.download(it, root, None, threading.Event()) == dest
    assert server.requests[-1][1] == "bytes=%d-" % cut      # only the rest
    assert _read(dest) == body
    assert not os.path.exists(dest + ".part")


def test_bad_sha_never_lands(server, tmp_path):
    it = _item(server, "b.bin", _blob(50_000), sha="0" * 64)
    root = _root(tmp_path)
    dest = os.path.join(root, "test", "b.bin")
    with pytest.raises(dl.DownloadError) as e:
        dl.download(it, root, None, threading.Event())
    assert e.value.code == "bozuk"
    assert "bozuk" in str(e.value)
    assert not os.path.exists(dest)
    assert not os.path.exists(dest + ".part")


def test_server_ignores_range_restarts_cleanly(server, tmp_path):
    body = _blob(120_000)
    it = _item(server, "i.bin", body)
    server.cut_after["/i.bin"] = 30_000
    root = _root(tmp_path)
    with pytest.raises(dl.DownloadError):
        dl.download(it, root, None, threading.Event())
    server.ignore_range = True          # 200 + the whole body
    dest = dl.download(it, root, None, threading.Event())
    assert server.requests[-1][1] == "bytes=30000-"
    assert _read(dest) == body     # not prefix + whole body


def test_corrupt_part_from_another_file_is_discarded(server, tmp_path):
    """A .part whose bytes do not belong to this file (e.g. the URL's file
    changed) fails the hash: deleted, so the next try starts clean."""
    body = _blob(60_000)
    it = _item(server, "c.bin", body)
    root = _root(tmp_path)
    dest = os.path.join(root, "test", "c.bin")
    os.makedirs(os.path.dirname(dest))
    with open(dest + ".part", "wb") as f:
        f.write(b"x" * 10_000)
    with pytest.raises(dl.DownloadError) as e:
        dl.download(it, root, None, threading.Event())
    assert e.value.code == "bozuk"
    assert not os.path.exists(dest + ".part")
    assert dl.download(it, root, None, threading.Event()) == dest
    assert _read(dest) == body


def test_oversized_part_is_discarded_and_restarted(server, tmp_path):
    body = _blob(20_000)
    it = _item(server, "o.bin", body)
    root = _root(tmp_path)
    dest = os.path.join(root, "test", "o.bin")
    os.makedirs(os.path.dirname(dest))
    with open(dest + ".part", "wb") as f:
        f.write(b"y" * 30_000)
    assert dl.download(it, root, None, threading.Event()) == dest
    assert server.requests[-1][1] is None
    assert _read(dest) == body


def test_cancel_leaves_part_file(server, tmp_path):
    body = _blob(400_000)
    it = _item(server, "k.bin", body)
    server.chunk_delay = 0.01
    root = _root(tmp_path)
    dest = os.path.join(root, "test", "k.bin")
    cancel = threading.Event()

    def progress(done, total):
        if done >= 50_000:
            cancel.set()
    with pytest.raises(dl.DownloadCancelled) as e:
        dl.download(it, root, progress, cancel)
    assert e.value.code == "iptal"
    assert isinstance(e.value, dl.DownloadError)
    have = os.path.getsize(dest + ".part")
    assert 0 < have < len(body)
    assert not os.path.exists(dest)

    server.chunk_delay = 0.0
    assert dl.download(it, root, None, threading.Event()) == dest
    assert server.requests[-1][1] == "bytes=%d-" % have
    assert _read(dest) == body


def test_progress_is_rate_limited_and_ends_complete(server, tmp_path,
                                                    monkeypatch):
    body = _blob(300_000)
    it = _item(server, "p.bin", body)
    calls = []
    monkeypatch.setattr(dl, "PROGRESS_INTERVAL", 3600.0)
    dl.download(it, _root(tmp_path), lambda d, t: calls.append((d, t)),
                threading.Event())
    # 300 kB is many chunks, yet only the first and last report get through.
    assert calls == [(0, len(body)), (len(body), len(body))]


def test_existing_file_is_not_downloaded_again(server, tmp_path):
    body = _blob(10_000)
    it = _item(server, "e.bin", body)
    root = _root(tmp_path)
    dl.download(it, root, None, threading.Event())
    n = len(server.requests)
    dl.download(it, root, None, threading.Event())
    assert len(server.requests) == n


# ------------------------------------------------------------- failures ---

def test_http_error_is_turkish_and_console_ascii(server, tmp_path, capsys):
    it = mm.Item("yok.bin", server.url("yok.bin"), 10, "0" * 64,
                 "test/yok.bin", "file")
    with pytest.raises(dl.DownloadError) as e:
        dl.download(it, _root(tmp_path), None, threading.Event())
    assert e.value.code == "sunucu"
    assert "404" in str(e.value) and "Sunucu" in str(e.value)
    err = capsys.readouterr().err
    assert "[indir]" in err and err.isascii()


def test_read_timeout_does_not_hang(server, tmp_path, monkeypatch):
    it = _item(server, "h.bin", _blob(1000))
    server.hang = True
    monkeypatch.setattr(dl, "TIMEOUT", 0.5)
    t0 = time.monotonic()
    with pytest.raises(dl.DownloadError) as e:
        dl.download(it, _root(tmp_path), None, threading.Event())
    assert time.monotonic() - t0 < 5
    assert e.value.code == "zamanasimi"


def test_disk_full_before_start_is_turkish(server, tmp_path, monkeypatch,
                                           capsys):
    it = _item(server, "d.bin", _blob(5000))
    monkeypatch.setattr(dl.shutil, "disk_usage",
                        lambda p: type("U", (), {"free": 100})())
    with pytest.raises(dl.DownloadError) as e:
        dl.download(it, _root(tmp_path), None, threading.Event())
    assert e.value.code == "disk"
    assert "Diskte yer" in str(e.value)
    assert server.requests == []            # nothing was fetched
    assert capsys.readouterr().err.isascii()


class _FullDisk(io.RawIOBase):
    def __init__(self, real):
        self.real = real

    def write(self, b):
        raise OSError(errno.ENOSPC, "No space left on device")

    def close(self):
        self.real.close()
        super().close()


def test_disk_full_while_writing_keeps_part(server, tmp_path, monkeypatch):
    it = _item(server, "f.bin", _blob(5000))
    real_open = dl._open_part
    monkeypatch.setattr(dl, "_open_part",
                        lambda p, mode: _FullDisk(real_open(p, mode)))
    root = _root(tmp_path)
    with pytest.raises(dl.DownloadError) as e:
        dl.download(it, root, None, threading.Event())
    assert e.value.code == "disk"
    assert "Diskte yer" in str(e.value)
    assert os.path.exists(os.path.join(root, "test", "f.bin.part"))


def test_permission_denied_is_turkish(server, tmp_path, monkeypatch):
    it = _item(server, "z.bin", _blob(5000))

    def denied(p, mode):
        raise PermissionError(errno.EACCES, "Permission denied", p)
    monkeypatch.setattr(dl, "_open_part", denied)
    with pytest.raises(dl.DownloadError) as e:
        dl.download(it, _root(tmp_path), None, threading.Event())
    assert e.value.code == "izin"
    assert "izni yok" in str(e.value)


def test_proxy_from_getproxies_is_used(server, tmp_path, monkeypatch):
    body = _blob(3000)
    server.files["/via.bin"] = body
    monkeypatch.setattr(urllib.request, "getproxies",
                        lambda: {"http": "http://127.0.0.1:%d" % server.port})
    it = mm.Item("via.bin", "http://indirme.invalid/via.bin", len(body),
                 hashlib.sha256(body).hexdigest(), "test/via.bin", "file")
    dest = dl.download(it, _root(tmp_path), None, threading.Event())
    assert _read(dest) == body
    assert server.requests[-1][2] is True       # came as a proxy request


def test_ssl_context_merges_certifi(monkeypatch):
    certifi = pytest.importorskip("certifi")
    with open(certifi.where(), "rb") as f:
        n = f.read().count(b"BEGIN CERTIFICATE")
    ctx = dl._ssl_context()
    assert ctx.verify_mode == dl.ssl.CERT_REQUIRED
    assert len(ctx.get_ca_certs()) >= n


def test_ssl_context_without_certifi_uses_the_default(monkeypatch):
    monkeypatch.setitem(sys.modules, "certifi", None)    # import fails
    ctx = dl._ssl_context()
    assert ctx.verify_mode == dl.ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


# ------------------------------------------------------------ wheel libs ---

def _wheel(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(zipfile.ZipInfo(name), data)
    return buf.getvalue()


def test_wheel_libs_extracts_only_libs(server, tmp_path):
    whl = _wheel([
        ("nvidia/cublas/lib/libcublas.so.12", b"so12"),
        ("nvidia/cublas/lib/libcublasLt.so.12", b"lt12"),
        ("nvidia/cuda_nvrtc/lib/libnvrtc-builtins.so.12.8", b"builtins"),
        ("nvidia/cublas/bin/cublas64_12.dll", b"dll"),
        ("nvidia/cublas/lib/cublas.lib", b"import lib"),
        ("nvidia/cublas/include/cublas_v2.h", b"header"),
        ("nvidia/cublas/__init__.py", b""),
        ("nvidia_cublas_cu12-1.0.dist-info/RECORD", b"record"),
        ("nvidia/libtop.so", b"not under lib/ or bin/"),
    ])
    it = _item(server, "w-1.0-py3-none-any.whl", whl, kind="wheel_libs",
               dest_rel="gpu/w-1.0-py3-none-any.whl")
    root = _root(tmp_path)
    got = dl.download(it, root, None, threading.Event())
    libdir = os.path.join(root, "gpu")
    assert got == libdir
    libs = sorted(f for f in os.listdir(libdir) if not f.startswith("."))
    assert libs == ["cublas64_12.dll", "libcublas.so.12", "libcublasLt.so.12",
                    "libnvrtc-builtins.so.12.8"]
    assert _read(os.path.join(libdir, "libcublas.so.12")) == b"so12"
    # The ~GB wheel is not kept once its libraries are out.
    assert not os.path.exists(os.path.join(libdir, "w-1.0-py3-none-any.whl"))
    n = len(server.requests)
    assert dl.download(it, root, None, threading.Event()) == libdir
    assert len(server.requests) == n


def test_accel_finds_the_extracted_libraries(server, tmp_path, monkeypatch):
    """The flat gpu/ folder holds exactly the names accel.preload_cuda
    requires (loading is faked: no CUDA library is touched)."""
    from eye_gaze import accel
    osname = "win" if os.name == "nt" else "linux"
    need = "ctranslate2" if osname == "win" else "onnxruntime"
    names = accel._REQUIRED[need][osname]
    sub = "bin" if osname == "win" else "lib"
    whl = _wheel([("nvidia/pkg/%s/%s" % (sub, n), b"x") for n in names])
    it = _item(server, "all.whl", whl, kind="wheel_libs", dest_rel="gpu/all.whl")
    libdir = dl.download(it, _root(tmp_path), None, threading.Event())
    loaded = []

    def cdll(target):
        if not os.path.isabs(target):
            raise OSError("not on the system path")
        loaded.append(target)
    monkeypatch.setattr(accel, "_preloaded", {})
    monkeypatch.setattr(accel, "_handles", [])
    monkeypatch.setattr(accel, "_cdll", cdll)
    monkeypatch.setattr(accel.os, "add_dll_directory",
                        lambda d: None, raising=False)
    assert accel.preload_cuda(libdir, need) is True
    assert {os.path.dirname(t) for t in loaded} == {libdir}


@pytest.mark.parametrize("evil", [
    "../evil.dll",
    "nvidia/x/lib/../../../evil.so.1",
    "/abs/nvidia/x/lib/evil.so",
    "C:/abs/nvidia/x/bin/evil.dll",
    "nvidia\\x\\lib\\..\\..\\..\\evil.so",
])
def test_wheel_with_traversal_member_is_refused(server, tmp_path, evil):
    whl = _wheel([("nvidia/ok/lib/libok.so.1", b"ok"), (evil, b"evil")])
    it = _item(server, "t.whl", whl, kind="wheel_libs", dest_rel="gpu/t.whl")
    root = _root(tmp_path)
    with pytest.raises(dl.DownloadError) as e:
        dl.download(it, root, None, threading.Event())
    assert e.value.code == "paket"
    found = [os.path.join(d, f) for d, _, fs in os.walk(tmp_path) for f in fs]
    assert not any("evil" in f for f in found)
    assert not os.path.exists(os.path.join(root, "gpu", "libok.so.1"))
    assert dl.item_status(it, root) == "missing"


# ------------------------------------------------------------- manifest ---

def test_manifest_entries_have_size_and_sha():
    assert set(mm.MANIFEST) >= {"whisper-tiny", "whisper-base",
                                "whisper-small", "whisper-medium",
                                "nvidia-gpu-win", "nvidia-gpu-linux",
                                "piper-tr-dfki-medium"}
    for name in mm.MANIFEST:
        its = mm.items(name)
        assert its, name
        for it in its:
            assert re.fullmatch(r"[0-9a-f]{64}", it.sha256), it
            assert isinstance(it.size, int) and it.size > 0, it
            assert it.kind in mm.KINDS, it
            assert it.url.startswith("https://"), it
            parts = it.dest_rel.split("/")
            assert "" not in parts and ".." not in parts and "\\" not in \
                it.dest_rel, it
        # ensure() hands back one folder per entry.
        assert len({it.dest_rel.rsplit("/", 1)[0] for it in its}) == 1, name


def test_manifest_whisper_has_exactly_the_faster_whisper_files():
    for size in ("tiny", "base", "small", "medium"):
        its = mm.items("whisper-" + size)
        assert sorted(it.dest_rel.rsplit("/", 1)[1] for it in its) == [
            "config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]
        for it in its:
            assert it.kind == "hf_dir"
            assert re.search(r"/Systran/faster-whisper-%s/resolve/[0-9a-f]{40}/"
                             % size, it.url), it.url


_CT2_WHEELS = {"nvidia-cublas-cu12", "nvidia-cudnn-cu12"}
_ORT_WHEELS = _CT2_WHEELS | {"nvidia-cuda-runtime-cu12", "nvidia-cufft-cu12",
                             "nvidia-curand-cu12", "nvidia-nvjitlink-cu12",
                             "nvidia-cuda-nvrtc-cu12"}


@pytest.mark.parametrize("pack,plat,wheels", [
    # Windows: only CTranslate2 uses CUDA (onnxruntime is DirectML/CPU).
    ("nvidia-gpu-win", "win_amd64", _CT2_WHEELS),
    ("nvidia-gpu-linux", "manylinux", _ORT_WHEELS),
])
def test_manifest_gpu_pack_has_what_its_consumers_need(pack, plat, wheels):
    its = mm.items(pack)
    assert {it.name.split("==")[0] for it in its} == wheels
    for it in its:
        assert it.kind == "wheel_libs" and it.dest_rel.startswith("gpu/")
        assert plat in it.url and it.url.endswith(".whl")
        assert "x86_64" in it.url or plat == "win_amd64"
        assert it.url.startswith("https://files.pythonhosted.org/")


def test_gpu_pack_matches_the_os():
    assert mm.gpu_pack() == ("nvidia-gpu-win" if os.name == "nt"
                             else "nvidia-gpu-linux")


# --------------------------------------------------------- ensure/status ---

@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "veri-ı"
    monkeypatch.setenv(paths.ENV_DATA_DIR, str(d))
    monkeypatch.setattr(paths, "_fallbacks", {})
    return d


def _closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_ensure_offline_returns_none_with_reason(data_dir, monkeypatch,
                                                 capsys):
    it = mm.Item("x.bin", "http://127.0.0.1:%d/x.bin" % _closed_port(), 10,
                 "0" * 64, "test/x.bin", "file")
    monkeypatch.setitem(mm.MANIFEST, "test-offline", it)
    assert dl.ensure("test-offline") is None
    assert "bağlanılamadı" in dl.last_error
    err = capsys.readouterr().err
    assert "[indir]" in err and err.isascii()
    assert dl.status("test-offline") == "missing"


def test_ensure_downloads_a_list_into_one_folder(server, data_dir,
                                                 monkeypatch):
    a, b = _blob(30_000, 1), _blob(20_000, 2)
    its = [_item(server, "one.bin", a, dest_rel="paket/one.bin"),
           _item(server, "two.bin", b, dest_rel="paket/two.bin")]
    monkeypatch.setitem(mm.MANIFEST, "test-list", its)
    calls = []
    assert dl.status("test-list") == "missing"
    got = dl.ensure("test-list", lambda d, t: calls.append((d, t)))
    assert got == os.path.join(paths.model_dir(), "paket")
    assert dl.status("test-list") == "ok"
    assert dl.last_error is None
    # One progress bar over both files.
    assert {t for _, t in calls} == {len(a) + len(b)}
    assert calls[-1] == (len(a) + len(b), len(a) + len(b))
    n = len(server.requests)
    assert dl.ensure("test-list") == got          # present: no network
    assert len(server.requests) == n


def test_status_partial_while_a_part_file_exists(server, data_dir,
                                                 monkeypatch):
    it = _item(server, "s.bin", _blob(50_000), dest_rel="s/s.bin")
    server.cut_after["/s.bin"] = 10_000
    monkeypatch.setitem(mm.MANIFEST, "test-s", it)
    monkeypatch.setattr(dl, "ATTEMPTS", 1)
    assert dl.ensure("test-s") is None
    assert "yarıda" in dl.last_error
    assert dl.status("test-s") == "partial"
    assert dl.ensure("test-s") == os.path.join(paths.model_dir(), "s", "s.bin")
    assert dl.status("test-s") == "ok"


def test_ensure_retries_a_cut_connection_from_where_it_stopped(
        server, data_dir, monkeypatch):
    body = _blob(80_000)
    it = _item(server, "cut.bin", body, dest_rel="c/cut.bin")
    server.cut_after["/cut.bin"] = 20_000
    monkeypatch.setitem(mm.MANIFEST, "test-cut", it)
    dest = dl.ensure("test-cut")
    assert _read(dest) == body
    assert [r[1] for r in server.requests] == [None, "bytes=20000-"]


@pytest.fixture
def ascii_root(tmp_path, monkeypatch):
    """paths' ASCII fallback root, redirected into tmp_path (both the
    creating and the computing variant)."""
    root = str(tmp_path / "ascii-kok")
    monkeypatch.setattr(paths, "_ascii_root", lambda: root)
    monkeypatch.setattr(paths, "_nominal_ascii_root", lambda: root)
    return root


def _whisper_entry(server, monkeypatch):
    its = [_item(server, n, _blob(1000 + i), kind="hf_dir",
                 dest_rel="whisper/faster-whisper-test/" + n)
           for i, n in enumerate(("config.json", "model.bin"))]
    monkeypatch.setitem(mm.MANIFEST, "whisper-test", its)


def test_whisper_folder_goes_to_the_ascii_root_on_windows(
        server, data_dir, ascii_root, monkeypatch):
    """Windows CTranslate2 opens the model with narrow-char APIs: with a
    non-ASCII model_dir the folder goes under paths' ASCII root."""
    monkeypatch.setattr(dl, "_is_windows", lambda: True)
    _whisper_entry(server, monkeypatch)
    got = dl.ensure("whisper-test")
    assert got == os.path.join(ascii_root, "whisper", "faster-whisper-test")
    assert sorted(os.listdir(got)) == ["config.json", "model.bin"]
    assert dl.status("whisper-test") == "ok"


def test_whisper_folder_stays_in_model_dir_on_linux(
        server, data_dir, ascii_root, monkeypatch):
    """Linux hands UTF-8 paths to every library, and its ASCII fallback
    (XDG_RUNTIME_DIR, /tmp) is wiped at logout: the model stays in the
    persistent model_dir even when that is not ASCII."""
    monkeypatch.setattr(dl, "_is_windows", lambda: False)
    _whisper_entry(server, monkeypatch)
    got = dl.ensure("whisper-test")
    assert not paths.model_dir().isascii()
    assert got == os.path.join(paths.model_dir(), "whisper",
                               "faster-whisper-test")
    assert sorted(os.listdir(got)) == ["config.json", "model.bin"]
    assert dl.status("whisper-test") == "ok"
    assert not os.path.exists(ascii_root)


@pytest.mark.parametrize("windows", [True, False])
def test_status_creates_nothing(data_dir, ascii_root, monkeypatch, windows):
    monkeypatch.setattr(dl, "_is_windows", lambda: windows)
    for name in mm.MANIFEST:
        assert dl.status(name) == "missing", name
    assert not os.path.exists(data_dir)
    assert not os.path.exists(ascii_root)


def test_concurrent_ensure_downloads_once(server, data_dir, monkeypatch):
    body = _blob(200_000)
    it = _item(server, "cc.bin", body, dest_rel="cc/cc.bin")
    server.chunk_delay = 0.005
    monkeypatch.setitem(mm.MANIFEST, "test-cc", it)
    results, errors = [], []

    def run():
        try:
            results.append(dl.ensure("test-cc"))
        except Exception as e:      # pragma: no cover - reported below
            errors.append(e)
    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors
    assert len(results) == 2 and results[0] == results[1] is not None
    assert _read(results[0]) == body
    assert len([r for r in server.requests if r[0] == "/cc.bin"]) == 1


def test_concurrent_download_of_one_item_does_not_corrupt(server, tmp_path):
    """download() itself serialises on the destination, whoever calls it."""
    body = _blob(200_000)
    it = _item(server, "dd.bin", body)
    server.chunk_delay = 0.005
    root = _root(tmp_path)
    out = []
    threads = [threading.Thread(target=lambda: out.append(
        dl.download(it, root, None, threading.Event()))) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert len(out) == 2
    assert _read(out[0]) == body


def test_range_past_the_servers_end_restarts_from_zero(server, tmp_path):
    """416: the .part is longer than what the server has, so it is not a
    prefix of this file - dropped, and the file is asked for from byte 0."""
    server.files["/kisa.bin"] = _blob(5000)
    it = mm.Item("kisa.bin", server.url("kisa.bin"), 10_000, "0" * 64,
                 "test/kisa.bin", "file")
    root = _root(tmp_path)
    part = os.path.join(root, "test", "kisa.bin.part")
    os.makedirs(os.path.dirname(part))
    with open(part, "wb") as f:
        f.write(b"z" * 8000)
    with pytest.raises(dl.DownloadError) as e:
        dl.download(it, root, None, threading.Event())
    assert e.value.code == "kesildi"
    assert [r[1] for r in server.requests] == ["bytes=8000-", None]
    assert _read(part) == _blob(5000)


# -------------------------------------------------------- two processes ---

_CHILD = r"""
import json, sys, threading, urllib.request
sys.path.insert(0, sys.argv[1])
urllib.request.getproxies = lambda: {}
from eye_gaze import downloader as dl
from eye_gaze import models_manifest as mm
mode, arg = sys.argv[2], json.loads(sys.argv[3])
if mode == "hold":
    lock = dl._ProcessLock(arg)
    assert lock.try_acquire()
    print("held", flush=True)
    sys.stdin.readline()            # parent says when (or kills us)
    lock.release()
else:
    item = mm.Item(**arg["item"])
    print(dl.download(item, arg["root"], None, threading.Event()), flush=True)
"""


def _child(mode, arg):
    import json
    import subprocess
    gaze_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, PYTHONIOENCODING="utf-8")    # paths hold "ı"
    return subprocess.Popen(
        [sys.executable, "-c", _CHILD, gaze_dir, mode, json.dumps(arg)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env)


def _reap(child):
    child.kill()
    child.wait(10)
    for f in (child.stdin, child.stdout, child.stderr):
        f.close()


def _download_in_thread(it, root):
    out = []
    t = threading.Thread(target=lambda: out.append(
        dl.download(it, root, None, threading.Event())), daemon=True)
    t.start()
    return t, out


def test_another_process_holding_the_item_makes_us_wait(server, tmp_path,
                                                        monkeypatch):
    monkeypatch.setattr(dl, "LOCK_POLL", 0.05)
    body = _blob(20_000)
    it = _item(server, "pl.bin", body)
    root = _root(tmp_path)
    dest = os.path.join(root, "test", "pl.bin")
    os.makedirs(os.path.dirname(dest))
    child = _child("hold", dest + ".lock")
    try:
        assert child.stdout.readline().strip() == "held"
        t, out = _download_in_thread(it, root)
        t.join(0.6)
        assert t.is_alive() and server.requests == []     # waiting
        child.stdin.write("\n")
        child.stdin.flush()
        t.join(10)
        assert out == [dest] and _read(dest) == body
    finally:
        _reap(child)


def test_a_crashed_holder_does_not_block(server, tmp_path, monkeypatch):
    """The lock is the OS's, not the file's: a killed holder leaves the lock
    file behind but nothing that blocks."""
    monkeypatch.setattr(dl, "LOCK_POLL", 0.05)
    body = _blob(20_000)
    it = _item(server, "cr.bin", body)
    root = _root(tmp_path)
    dest = os.path.join(root, "test", "cr.bin")
    os.makedirs(os.path.dirname(dest))
    child = _child("hold", dest + ".lock")
    try:
        assert child.stdout.readline().strip() == "held"
    finally:
        _reap(child)                # killed: no release of its own
    assert os.path.exists(dest + ".lock")
    t, out = _download_in_thread(it, root)
    t.join(10)
    assert out == [dest] and _read(dest) == body


def test_two_processes_download_one_item_once(server, tmp_path):
    body = _blob(300_000)
    it = _item(server, "two.bin", body)
    server.chunk_delay = 0.02       # ~0.75 s: both children overlap
    root = _root(tmp_path)
    arg = {"item": {f: getattr(it, f) for f in
                    ("name", "url", "size", "sha256", "dest_rel", "kind")},
           "root": root}
    kids = [_child("download", arg) for _ in range(2)]
    outs, errs = [], []
    for k in kids:
        try:
            o, e = k.communicate(timeout=60)
        finally:
            k.kill()
            k.wait(10)
        assert k.returncode == 0, e
        outs.append(o.strip())
        errs.append(e)
    # One of them really met the other's lock (and waited for it).
    assert sum("bekleniyor" in e for e in errs) == 1, errs
    dest = os.path.join(root, "test", "two.bin")
    assert outs == [dest, dest]
    assert _read(dest) == body
    assert len([r for r in server.requests if r[0] == "/two.bin"]) == 1


def test_present_file_needs_no_lock_file(server, tmp_path, monkeypatch):
    """A file in place is returned without writing anything next to it (a
    read-only portable folder must still work)."""
    body = _blob(5000)
    it = _item(server, "ro.bin", body)
    root = _root(tmp_path)
    dest = dl.download(it, root, None, threading.Event())

    def refuse(*a, **k):
        raise PermissionError(errno.EACCES, "read-only", a[0])
    monkeypatch.setattr(dl.os, "open", refuse)
    assert dl.download(it, root, None, threading.Event()) == dest



# ------------------------------------------- unpack order and GPU trust ---

def test_entry_libraries_are_unpacked_last(server, tmp_path, monkeypatch):
    """A crash mid-unpack must not leave the names accel requires next to
    missing dependencies: they come out after everything else."""
    whl = _wheel([
        ("nvidia/cudnn/lib/libcudnn.so.9", b"entry"),
        ("nvidia/cudnn/lib/libcudnn_engines_precompiled.so.9", b"dep"),
        ("nvidia/cudnn/lib/libcudnn_graph.so.9", b"dep"),
        ("nvidia/cublas/lib/libcublas.so.12", b"entry"),
        ("nvidia/cublas/lib/libnvblas.so.12", b"dep"),
    ])
    it = _item(server, "o.whl", whl, kind="wheel_libs", dest_rel="gpu/o.whl")
    order = []
    real = dl._open_part

    def spy(path, mode):
        order.append(os.path.basename(path))
        return real(path, mode)
    monkeypatch.setattr(dl, "_open_part", spy)
    dl.download(it, _root(tmp_path), None, threading.Event())
    libs = [n for n in order if n.endswith(".so.9.tmp") or
            n.endswith(".so.12.tmp")]
    assert libs[-2:] == [".libcublas.so.12.tmp", ".libcudnn.so.9.tmp"]
    assert all(n.startswith(".") for n in libs)     # hidden temporaries
    assert order[-1].endswith(".tamam.tmp")         # marker after all


def test_failed_unpack_leaves_no_temporary_and_no_marker(server, tmp_path,
                                                         monkeypatch):
    whl = _wheel([("nvidia/a/lib/liba.so.1", b"a" * 1000),
                  ("nvidia/a/lib/libnvrtc-builtins.so.12.8", b"b" * 1000)])
    it = _item(server, "f.whl", whl, kind="wheel_libs", dest_rel="gpu/f.whl")
    real = dl._open_part

    def fail_on_builtins(path, mode):
        f = real(path, mode)
        if "builtins" in path:
            f.close()
            raise OSError(errno.ENOSPC, "No space left on device")
        return f
    monkeypatch.setattr(dl, "_open_part", fail_on_builtins)
    root = _root(tmp_path)
    with pytest.raises(dl.DownloadError) as e:
        dl.download(it, root, None, threading.Event())
    assert e.value.code == "disk"
    left = os.listdir(os.path.join(root, "gpu"))
    assert not [n for n in left if n.endswith(".tmp")], left
    assert not [n for n in left if n.endswith(".tamam")], left
    assert dl.item_status(it, root) == "partial"    # the verified wheel stays


def _gpu_dir_with_required_libs(tmp_path, monkeypatch):
    from eye_gaze import accel
    monkeypatch.setattr(accel, "_is_windows", lambda: False)
    monkeypatch.setattr(accel, "_preloaded", {})
    monkeypatch.setattr(accel, "_handles", [])
    gpu = tmp_path / "gpu"
    gpu.mkdir()
    for name in accel._REQUIRED["onnxruntime"]["linux"]:
        (gpu / name).write_bytes(b"")
    monkeypatch.setattr(accel, "gpu_lib_dir", lambda: str(gpu))

    def cdll(target):
        if not os.path.isabs(target):
            raise OSError("not on the system path")
    monkeypatch.setattr(accel, "_cdll", cdll)
    return accel, gpu


def test_gpu_pack_folder_without_markers_is_not_cuda(tmp_path, monkeypatch,
                                                     capsys):
    """Every required library is there, but a marker is missing (the
    unpack was cut): accel must not offer CUDA from that folder."""
    accel, gpu = _gpu_dir_with_required_libs(tmp_path, monkeypatch)
    assert accel.providers("auto") == ["CPUExecutionProvider"]
    assert "GPU paketi yarim" in capsys.readouterr().err


def test_gpu_pack_folder_with_all_markers_is_cuda(tmp_path, monkeypatch):
    accel, gpu = _gpu_dir_with_required_libs(tmp_path, monkeypatch)
    for it in mm.items("nvidia-gpu-linux"):
        whl = os.path.join(str(gpu), it.dest_rel.rsplit("/", 1)[1])
        with open(dl._marker(whl), "w", encoding="utf-8") as f:
            f.write('{"sha256": "%s", "files": []}' % it.sha256)
    assert dl.pack_ready("nvidia-gpu-linux", str(gpu))
    assert accel.providers("auto") == ["CUDAExecutionProvider",
                                       "CPUExecutionProvider"]


# ------------------------------------------------------ waits and cancel ---

def test_windows_delete_pending_lock_file_is_busy_not_forbidden(
        server, tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "_is_windows", lambda: True)
    monkeypatch.setattr(dl, "LOCK_POLL", 0.01)
    body = _blob(3000)
    it = _item(server, "dp.bin", body)
    real = dl._ProcessLock.try_acquire
    denials = [2]

    def try_acquire(self):
        if denials[0]:
            denials[0] -= 1
            raise PermissionError(errno.EACCES, "Access is denied", self.path)
        return real(self)
    monkeypatch.setattr(dl._ProcessLock, "try_acquire", try_acquire)
    dest = dl.download(it, _root(tmp_path), None, threading.Event())
    assert _read(dest) == body


@pytest.mark.parametrize("windows", [True, False])
def test_lasting_lock_denial_is_a_permission_error(server, tmp_path,
                                                   monkeypatch, windows):
    monkeypatch.setattr(dl, "_is_windows", lambda: windows)
    monkeypatch.setattr(dl, "LOCK_POLL", 0.01)
    monkeypatch.setattr(dl, "LOCK_DENIED_POLLS", 3)
    calls = []

    def try_acquire(self):
        calls.append(1)
        raise PermissionError(errno.EACCES, "Access is denied", self.path)
    monkeypatch.setattr(dl._ProcessLock, "try_acquire", try_acquire)
    it = _item(server, "dn.bin", _blob(3000))
    with pytest.raises(dl.DownloadError) as e:
        dl.download(it, _root(tmp_path), None, threading.Event())
    assert e.value.code == "izin"
    assert len(calls) == (4 if windows else 1)


def test_cancel_ends_a_wait_for_another_thread(server, tmp_path,
                                               monkeypatch, data_dir):
    monkeypatch.setattr(dl, "LOCK_POLL", 0.02)
    it = _item(server, "w.bin", _blob(3000), dest_rel="w/w.bin")
    monkeypatch.setitem(mm.MANIFEST, "test-wait", it)
    root = _root(tmp_path)
    busy = dl._lock(os.path.normcase(os.path.abspath(
        os.path.join(root, "w", "w.bin"))))
    busy_ensure = dl._lock("ensure:test-wait")
    cancel = threading.Event()
    threading.Timer(0.2, cancel.set).start()
    with busy, busy_ensure:
        t0 = time.monotonic()
        with pytest.raises(dl.DownloadCancelled):
            dl.download(it, root, None, cancel)
        assert dl.ensure("test-wait", cancel=cancel) is None
        assert "durduruldu" in dl.last_error
        assert time.monotonic() - t0 < 5
    assert server.requests == []
