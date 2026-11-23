"""Lossless PDF compression. The source is opened read-only."""

from dataclasses import dataclass
from itertools import count
import os
from pathlib import Path
import tempfile

import pikepdf


_PERMISSION_SIGNATURES = ("/DocMDP", "/UR", "/UR3")


class PdfSkipped(Exception):
    """A document that needs to retain its original representation."""


@dataclass(frozen=True)
class Result:
    original_size: int
    candidate_size: int
    output: Path | None
    reading_copy: bool = False


def _dictionaries(pdf):
    # Indirect objects appear separately. Walk their inline children as well.
    for obj in pdf.objects:
        pending = [obj]
        while pending:
            current = pending.pop()
            if isinstance(current, pikepdf.Stream):
                current = current.stream_dict
            if isinstance(current, pikepdf.Dictionary):
                yield current
                children = (value for _, value in current.items())
            elif isinstance(current, pikepdf.Array):
                children = iter(current)
            else:
                continue
            pending.extend(child for child in children
                           if isinstance(child, (pikepdf.Dictionary, pikepdf.Array))
                           and not child.is_indirect)


def _form_nodes(pdf):
    # Only the form tree and page widgets describe interactive PDF fields.
    # Producers can also store unrelated field templates in private metadata.
    form = pdf.Root.get("/AcroForm")
    roots = form.get("/Fields") if isinstance(form, pikepdf.Dictionary) else None
    pending = list(roots) if isinstance(roots, pikepdf.Array) else []
    for page in pdf.pages:
        annotations = page.obj.get("/Annots")
        if isinstance(annotations, pikepdf.Array):
            pending.extend(item for item in annotations if isinstance(item, pikepdf.Dictionary)
                           and item.get("/Subtype") == pikepdf.Name.Widget)
    seen = set()
    while pending:
        node = pending.pop()
        if not isinstance(node, pikepdf.Dictionary):
            continue
        if node.is_indirect:
            if node.objgen in seen:
                continue
            seen.add(node.objgen)
        yield node
        parent = node.get("/Parent")
        if isinstance(parent, pikepdf.Dictionary):
            pending.append(parent)
        kids = node.get("/Kids")
        if isinstance(kids, pikepdf.Array):
            pending.extend(kids)


def _signature_parts(pdf):
    fields = []
    values = [node for node in _dictionaries(pdf)
              if node.get("/Type") in (pikepdf.Name.Sig, pikepdf.Name.DocTimeStamp)]
    permissions = pdf.Root.get("/Perms")
    if isinstance(permissions, pikepdf.Dictionary):
        for key in _PERMISSION_SIGNATURES:
            value = permissions.get(key)
            if isinstance(value, pikepdf.Dictionary):
                values.append(value)
    for node in _form_nodes(pdf):
        field = pikepdf.AcroFormField(node)
        if field.field_type != "/Sig":
            continue
        value = field.value
        if value is not None:
            fields.append(node)
            # /Type is optional. The field reference identifies an
            # untyped signature without touching unrelated private data.
            if isinstance(value, pikepdf.Dictionary):
                values.append(value)
    return fields, values


def _remove_keys(obj, *keys):
    for key in keys:
        if key in obj:
            del obj[key]


def _check_signature_appearance(widget):
    flags = int(widget.get("/F", 0))
    rect = widget.get("/Rect")
    if (flags & pikepdf.AnnotationFlag.hidden
            or (flags & pikepdf.AnnotationFlag.no_view
                and not flags & (pikepdf.AnnotationFlag.print | pikepdf.AnnotationFlag.toggle_no_view))):
        return
    if isinstance(rect, pikepdf.Array) and len(rect) == 4:
        if rect[0] == rect[2] or rect[1] == rect[3]:
            return  # Invisible signatures and document timestamps.
    ap = widget.get("/AP")
    normal = ap.get("/N") if isinstance(ap, pikepdf.Dictionary) else None
    if isinstance(normal, pikepdf.Dictionary):
        state = widget.get("/AS")
        if isinstance(state, pikepdf.Name):
            normal = normal.get(str(state))
    if not isinstance(normal, pikepdf.Stream):
        raise PdfSkipped("이 PDF에는 서명란을 그리는 데 필요한 표시 정보가 없습니다. "
                         "원본을 보존하고 열람용 압축본을 건너뛰었습니다.")


def _prepare_reading_copy(pdf):
    # Collect inherited values before changing any member of the field tree.
    fields, values = _signature_parts(pdf)
    if not (fields or values):
        return False
    for node in fields:
        if node.get("/Subtype") == pikepdf.Name.Widget:
            _check_signature_appearance(node)
        # Keep the widget's type and /AP. A reader may regenerate a different
        # type of field when /NeedAppearances is set. Clearing /V removes the
        # actual signature. The original signed field becomes read-only.
        if any(key in node for key in ("/FT", "/V", "/T", "/Kids", "/Ff")):
            node.Ff = pikepdf.AcroFormField(node).flags | int(pikepdf.FormFieldFlag.read_only)
        _remove_keys(node, "/V", "/DV", "/SV", "/Lock", "/A", "/AA")
    # Timestamp, certification and usage-rights signatures can also be reached
    # from outside the form tree. Discard their stale validation data.
    for value in values:
        _remove_keys(value, "/Type", "/ByteRange", "/Contents")
    permissions = pdf.Root.get("/Perms")
    if isinstance(permissions, pikepdf.Dictionary):
        _remove_keys(permissions, *_PERMISSION_SIGNATURES)
        if not len(permissions):
            del pdf.Root["/Perms"]
    _remove_keys(pdf.Root, "/DSS")
    form = pdf.Root.get("/AcroForm")
    if isinstance(form, pikepdf.Dictionary) and isinstance(form.get("/SigFlags"), int):
        form.SigFlags = form.SigFlags & ~2  # Drop AppendOnly; signature fields still exist.
    return True


def _publish(temp: Path, source: Path, reading_copy: bool) -> Path:
    # Both operations fail if the destination already exists, even when two
    # instances finish the same source simultaneously. rename also works on FAT.
    tag = "_열람용_압축" if reading_copy else "_압축"
    for n in count(1):
        suffix = "" if n == 1 else f" ({n})"
        target = source.with_name(f"{source.stem}{tag}{suffix}.pdf")
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
                pages = len(pdf.pages)
                if not pages or pdf.check_pdf_syntax() or pdf.get_warnings():
                    raise PdfSkipped("PDF의 내부 구조를 확인할 수 없어 압축본을 만들지 않았습니다.")
                reading_copy = _prepare_reading_copy(pdf)

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
                return Result(initial.st_size, size, None, reading_copy)
            with pikepdf.Pdf.open(temp, attempt_recovery=False) as saved:
                if len(saved.pages) != pages or saved.check_pdf_syntax() or saved.get_warnings():
                    raise PdfSkipped("압축 결과를 다시 확인하는 과정에서 문제가 발견되었습니다.")
                if reading_copy and any(_signature_parts(saved)):
                    raise PdfSkipped("열람용 압축본에서 기존 전자서명 정보를 분리할 수 없었습니다.")

            now = source.stat()
            if (not os.path.samestat(initial, now) or initial.st_size != now.st_size
                    or initial.st_mtime_ns != now.st_mtime_ns):
                raise PdfSkipped("작업 중 원본 파일이 변경되었습니다. 변경이 끝난 뒤 다시 선택해 주세요.")
            return Result(initial.st_size, size, _publish(temp, source, reading_copy), reading_copy)
    except pikepdf.PasswordError as error:
        raise PdfSkipped("암호 또는 사용 권한으로 보호된 PDF입니다.") from error
    except pikepdf.PdfError as error:
        raise PdfSkipped("PDF를 정상적으로 읽을 수 없어 압축본을 만들지 않았습니다.") from error
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
