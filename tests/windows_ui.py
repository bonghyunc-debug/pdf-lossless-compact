"""Exercise the actual packaged EXE through the system Open dialog."""

from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep

from pywinauto import Desktop
from pyhanko.pdf_utils.reader import PdfFileReader

from tests.samples import make_sample, render_snapshot
from tests.signed_samples import make_signed_sample, verify_original


def enter_file(dialog, path):
    dialog.wait("visible", timeout=45)
    # IFileDialog's filename ComboBox contains a UIA Edit. The older common
    # dialog instead exposes the filename field as an Edit directly.
    edits = dialog.descendants(control_type="Edit")
    matches = [e for e in edits if e.element_info.automation_id == "1148"
               or e.element_info.name.startswith("File name")]
    if len(matches) != 1:
        observed = [(e.element_info.automation_id, e.element_info.name) for e in edits]
        raise RuntimeError(f"Expected one filename edit, found {len(matches)}. Edits: {observed}")
    edit = matches[0]
    edit.set_edit_text(str(path))
    dialog.child_window(auto_id="1", control_type="Button").invoke()


def await_result(process, expected):
    desktop = Desktop(backend="win32")
    deadline = monotonic() + 60
    while monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"EXE exited before showing the result (code {process.returncode})")
        window = desktop.window(title=expected, class_name="TkTopLevel")
        if window.exists(timeout=0.3):
            window.close()
            if process.wait(timeout=15) != 0:
                raise RuntimeError("EXE returned a failure status.")
            return
        sleep(0.2)
    visible = [w.window_text() for w in desktop.windows() if w.window_text()]
    raise RuntimeError(f"Expected {expected!r}. Visible window titles: {visible}")


@contextmanager
def running_app(exe, *files):
    with subprocess.Popen([str(exe), *map(str, files)]) as process:
        try:
            yield process
        finally:
            if process.poll() is None:
                subprocess.run(["taskkill", "/t", "/f", "/pid", str(process.pid)], check=False)


def use_picker(exe, source, title):
    with running_app(exe) as process:
        picker = Desktop(backend="uia").window(title="압축할 PDF 선택", class_name="#32770")
        enter_file(picker, source)
        await_result(process, title)


def main():
    exe = Path(sys.argv[1]).resolve(strict=True)
    with TemporaryDirectory(prefix="pdf-gui-") as folder:
        source = Path(folder) / "한글 시험 문서.pdf"
        make_sample(source, precompressed=True)
        original = source.read_bytes()
        use_picker(exe, source, "PDF 압축 완료")
        output = source.with_name("한글 시험 문서_압축.pdf")
        assert source.read_bytes() == original
        assert 0 < output.stat().st_size < len(original)
        assert render_snapshot(source) == render_snapshot(output)
        print(f"Frozen EXE, native picker, Hangul filename: {len(original)} -> {output.stat().st_size} bytes")
        with running_app(exe, output) as process:
            await_result(process, "이미 충분히 압축된 PDF입니다")
        assert set(Path(folder).iterdir()) == {source, output}
        print("Already-compressed result window verified. Original and output remain intact.")
        signed = Path(folder) / "전자서명 두 개.pdf"
        certs = make_signed_sample(signed, separate_widget=True, two_signers=True, needs_appearances=True)
        verify_original(signed, certs)
        signed_bytes = signed.read_bytes()
        use_picker(exe, signed, "열람용 PDF 압축 완료")
        copy = signed.with_name("전자서명 두 개_열람용_압축.pdf")
        assert signed.read_bytes() == signed_bytes and copy.stat().st_size < len(signed_bytes)
        verify_original(signed, certs)
        assert render_snapshot(signed) == render_snapshot(copy)
        with copy.open("rb") as stream:
            assert not PdfFileReader(stream).embedded_signatures
        print(f"Frozen EXE, signed PDF: {len(signed_bytes)} -> {copy.stat().st_size} bytes, "
              "both original signatures valid, reading copy unsigned, rendered pages identical.")
    print(f"EXE SHA256 {sha256(exe.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
