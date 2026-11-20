"""Lossless PDF compression. The source is opened read-only."""

from dataclasses import dataclass
from itertools import count
import os
from pathlib import Path
import tempfile

import pikepdf


class PdfSkipped(Exception):
    """A document that needs to retain its original representation."""


@dataclass(frozen=True)
class Result:
    original_size: int
    candidate_size: int
    output: Path | None


def _has_signature(pdf: pikepdf.Pdf) -> bool:
    # Visit each indirect object once. A signature value may also be a direct
    # dictionary inside a field, an array, or a catalog permission dictionary.
    for obj in pdf.objects:
        pending = [obj]
        while pending:
            current = pending.pop()
            if isinstance(current, pikepdf.Stream):
                current = current.stream_dict
            if isinstance(current, pikepdf.Dictionary):
                if (current.get("/Type") in (pikepdf.Name.Sig, pikepdf.Name.DocTimeStamp)
                        or ("/ByteRange" in current and "/Contents" in current)
                        or (current.get("/FT") == pikepdf.Name.Sig and current.get("/V") is not None)):
                    return True
                children = (value for _, value in current.items())
            elif isinstance(current, pikepdf.Array):
                children = iter(current)
            else:
                continue
            pending.extend(child for child in children
                           if isinstance(child, (pikepdf.Dictionary, pikepdf.Array))
                           and not child.is_indirect)
    return False


def _publish(temp: Path, source: Path) -> Path:
    # Both operations fail if the destination already exists, even when two
    # instances finish the same source simultaneously. rename also works on FAT.
    for n in count(1):
        suffix = "" if n == 1 else f" ({n})"
        target = source.with_name(f"{source.stem}_압축{suffix}.pdf")
        try:
            if os.name == "nt":
                os.rename(temp, target)
            else:
                os.link(temp, target)
        except FileExistsError:
            continue
        return target


def compress(source: Path) -> Result:
    source = Path(source).absolute()
    temp = None
    try:
        with source.open("rb") as original:
            initial = os.fstat(original.fileno())
            with pikepdf.Pdf.open(original, attempt_recovery=False) as pdf:
                if pdf.is_encrypted:
                    raise PdfSkipped("암호 또는 사용 권한으로 보호된 PDF입니다.")
                if _has_signature(pdf):
                    raise PdfSkipped("전자서명이 있는 PDF입니다. 서명의 유효성을 보존하기 위해 건너뛰었습니다.")

                pages = len(pdf.pages)
                if not pages or pdf.check_pdf_syntax() or pdf.get_warnings():
                    raise PdfSkipped("PDF의 내부 구조를 확인할 수 없어 압축본을 만들지 않았습니다.")

                # Keep the input's reader compatibility and leave XMP alone.
                already_supports_streams = tuple(map(int, pdf.pdf_version.split("."))) >= (1, 5)
                mode = (pikepdf.ObjectStreamMode.generate if already_supports_streams
                        else pikepdf.ObjectStreamMode.preserve)
                pikepdf.settings.set_flate_compression_level(9)
                with tempfile.NamedTemporaryFile(dir=source.parent, prefix=".pdf-compress-",
                                                 suffix=".tmp", delete=False) as staging:
                    temp = Path(staging.name)
                    pdf.save(staging, compress_streams=True, recompress_flate=True,
                             stream_decode_level=pikepdf.StreamDecodeLevel.generalized,
                             object_stream_mode=mode, preserve_pdfa=True,
                             fix_metadata_version=False, normalize_content=False)
                if pdf.get_warnings():
                    raise PdfSkipped("압축 중 PDF 구조 경고가 발생해 압축본을 만들지 않았습니다.")

            size = temp.stat().st_size
            if size >= initial.st_size:
                return Result(initial.st_size, size, None)
            with pikepdf.Pdf.open(temp, attempt_recovery=False) as saved:
                if len(saved.pages) != pages or saved.check_pdf_syntax() or saved.get_warnings():
                    raise PdfSkipped("압축 결과를 다시 확인하는 과정에서 문제가 발견되었습니다.")

            now = source.stat()
            if (not os.path.samestat(initial, now) or initial.st_size != now.st_size
                    or initial.st_mtime_ns != now.st_mtime_ns):
                raise PdfSkipped("작업 중 원본 파일이 변경되었습니다. 변경이 끝난 뒤 다시 선택해 주세요.")
            return Result(initial.st_size, size, _publish(temp, source))
    except pikepdf.PasswordError as error:
        raise PdfSkipped("암호 또는 사용 권한으로 보호된 PDF입니다.") from error
    except pikepdf.PdfError as error:
        raise PdfSkipped("PDF를 정상적으로 읽을 수 없어 압축본을 만들지 않았습니다.") from error
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
