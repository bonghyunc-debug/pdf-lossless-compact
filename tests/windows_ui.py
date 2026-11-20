"""Exercise the actual packaged EXE through the system Open dialog."""

from hashlib import sha256
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep

from pywinauto import Desktop

from tests.samples import make_sample, render_snapshot


def enter_file(dialog, path):
    dialog.wait("visible", timeout=45)
    # IFileDialog's filename ComboBox contains a UIA Edit. The older common
    # dialog instead exposes the filename field as an Edit directly.
    filename = dialog.child_window(auto_id="1148")
    if filename.exists(timeout=2):
        wrapper = filename.wrapper_object()
        if wrapper.element_info.control_type == "Edit":
            edit = wrapper
        else:
            edit = wrapper.descendants(control_type="Edit")[0]
    else:
        edits = dialog.descendants(control_type="Edit")
        edit = next(e for e in edits if e.element_info.name.startswith("File name"))
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


def main():
    exe = Path(sys.argv[1]).resolve(strict=True)
    with TemporaryDirectory(prefix="pdf-gui-") as folder:
        source = Path(folder) / "한글 시험 문서.pdf"
        make_sample(source, precompressed=True)
        original = source.read_bytes()
        with subprocess.Popen([str(exe)]) as process:
            try:
                picker = Desktop(backend="uia").window(title="압축할 PDF 선택", class_name="#32770")
                enter_file(picker, source)
                await_result(process, "PDF 압축 완료")
            finally:
                if process.poll() is None:
                    subprocess.run(["taskkill", "/t", "/f", "/pid", str(process.pid)], check=False)
        output = source.with_name("한글 시험 문서_압축.pdf")
        assert source.read_bytes() == original
        assert 0 < output.stat().st_size < len(original)
        assert render_snapshot(source) == render_snapshot(output)
        print(f"Frozen EXE, native picker, Hangul filename: {len(original)} -> {output.stat().st_size} bytes")
        with subprocess.Popen([str(exe), str(output)]) as process:
            try:
                await_result(process, "이미 충분히 압축된 PDF입니다")
            finally:
                if process.poll() is None:
                    subprocess.run(["taskkill", "/t", "/f", "/pid", str(process.pid)], check=False)
        assert set(Path(folder).iterdir()) == {source, output}
        print("Already-compressed result window verified. Original and output remain intact.")
    print(f"EXE SHA256 {sha256(exe.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
