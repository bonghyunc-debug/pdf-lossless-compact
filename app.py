"""Windows desktop entry point for PDF 용량 줄이기."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, ttk

from compress import PdfSkipped, compress


TITLE = "PDF 용량 줄이기"


def size_text(size: int) -> str:
    if size < 1024:
        return f"{size:,} B"
    if size < 1024 ** 2:
        return f"{size / 1024:,.1f} KB"
    return f"{size / 1024 ** 2:,.2f} MB"


class Window:
    def __init__(self, root, worker):
        self.root = root
        self.worker = worker
        self.running = False
        self.close_requested = False
        root.title(TITLE)
        root.resizable(True, False)
        root.protocol("WM_DELETE_WINDOW", self.close)
        panel = ttk.Frame(root, padding=(24, 20))
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(0, weight=1)
        self.heading = ttk.Label(panel, font=("Malgun Gothic", 13, "bold"))
        self.heading.grid(row=0, column=0, sticky="w")
        self.detail = ttk.Label(panel, justify="left", wraplength=460)
        self.detail.grid(row=1, column=0, sticky="ew", pady=(8, 12))
        self.progress = ttk.Progressbar(panel, mode="indeterminate", length=370)
        self.progress.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        controls = ttk.Frame(panel)
        controls.grid(row=3, column=0, sticky="e")
        self.choose_button = ttk.Button(controls, text="다른 PDF 선택", command=self.choose)
        self.choose_button.pack(side="left", padx=(0, 6))
        self.close_button = ttk.Button(controls, text="닫기", command=self.close)
        self.close_button.pack(side="left")

    def choose(self, first=False):
        selected = filedialog.askopenfilename(parent=self.root, title="압축할 PDF 선택",
                                              filetypes=[("PDF 문서", "*.pdf")])
        if selected:
            self.start(Path(selected))
        elif first:
            self.root.destroy()

    def start(self, source):
        self.running = True
        self.root.title(TITLE)
        self.heading.configure(text="PDF 압축 중…")
        self.detail.configure(text=source.name)
        self.choose_button.state(["disabled"])
        self.progress.grid()
        self.progress.start(14)
        self.root.deiconify()
        future = self.worker.submit(compress, source)
        self.root.after(100, self.finish, future, source)

    def finish(self, future, source):
        if not future.done():
            self.root.after(100, self.finish, future, source)
            return
        self.running = False
        self.progress.stop()
        self.progress.grid_remove()
        try:
            result = future.result()
        except PdfSkipped as error:
            heading, detail = "원본 유지", str(error)
        except OSError as error:
            heading = "파일을 처리할 수 없습니다"
            detail = f"파일과 폴더의 접근 권한 및 여유 공간을 확인해 주세요.\n{error}"
        except Exception as error:
            heading, detail = "압축 중 오류가 발생했습니다", f"{type(error).__name__}: {error}"
        else:
            if result.output:
                saved = result.original_size - result.candidate_size
                percent = 100 * saved / result.original_size
                heading = "열람용 PDF 압축 완료" if result.reading_copy else "PDF 압축 완료"
                detail = (f"{size_text(result.original_size)} → {size_text(result.candidate_size)}\n"
                          f"{size_text(saved)} 절약 ({percent:.1f}%)\n\n"
                          f"원본 폴더에 저장했습니다.\n{result.output.name}")
                if result.reading_copy:
                    detail += "\n\n전자서명을 검증할 때는 원본 PDF를 사용해 주세요."
            else:
                heading = "이미 충분히 압축된 PDF입니다"
                detail = (f"{source.name}\n원본 크기 {size_text(result.original_size)}\n\n"
                          "화질을 유지하면서 더 줄일 수 없어 원본만 유지했습니다.")
        if self.close_requested:
            self.root.destroy()
            return
        self.root.title(heading)
        self.heading.configure(text=heading)
        self.detail.configure(text=detail)
        self.choose_button.state(["!disabled"])
        self.close_button.focus_set()

    def close(self):
        if self.running:
            self.close_requested = True
            self.heading.configure(text="압축을 마치면 창을 닫습니다")
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    root.withdraw()
    with ThreadPoolExecutor(max_workers=1) as worker:
        window = Window(root, worker)
        if len(sys.argv) == 2:
            root.after_idle(window.start, Path(sys.argv[1]))
        else:
            root.after_idle(window.choose, True)
        root.mainloop()


if __name__ == "__main__":
    main()
