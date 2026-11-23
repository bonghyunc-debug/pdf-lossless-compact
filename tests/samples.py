"""Small, original fixtures made entirely from geometric shapes and test text."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
import pikepdf
from pikepdf import Array, Dictionary, Name


def korean_font(pdf):
    # Two embedded Type 3 glyphs, with a real Unicode map for search / copy.
    # The outlines are drawn directly rather than relying on system fonts.
    strokes = {
        "/Han": (b"65 720 m 465 720 l 180 805 m 340 805 l "
                 b"145 575 m 145 690 390 690 390 575 c 390 460 145 460 145 575 c "
                 b"595 825 m 595 390 l 595 630 m 785 630 l 190 380 m 190 215 l 670 215 l"),
        "/Geul": (b"90 785 m 710 785 l 710 590 l 95 540 m 760 540 l "
                  b"175 440 m 675 440 l 675 330 l 175 330 l 175 205 l 685 205 l"),
    }
    programs = Dictionary()
    for name, paths in strokes.items():
        programs[name] = pdf.make_stream(b"900 0 0 0 900 900 d1 38 w " + paths + b" S")
    cmap = pdf.make_stream(
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
        b"/CIDSystemInfo << /Registry (Test) /Ordering (Glyphs) /Supplement 0 >> def "
        b"/CMapName /Sample def /CMapType 2 def "
        b"1 begincodespacerange <00> <ff> endcodespacerange "
        b"2 beginbfchar <01> <D55C> <02> <AE00> endbfchar "
        b"endcmap CMapName currentdict /CMap defineresource pop end end")
    return pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.Type3, FontBBox=Array([0, 0, 900, 900]),
        FontMatrix=Array([.001, 0, 0, .001, 0, 0]), FirstChar=1, LastChar=2,
        Widths=Array([900, 900]), Encoding=Dictionary(
            Type=Name.Encoding, Differences=Array([1, Name.Han, Name.Geul])),
        CharProcs=programs, Resources=Dictionary(), ToUnicode=cmap))


def scan_image(pdf):
    im = Image.new("RGB", (720, 480), (247, 245, 235))
    draw = ImageDraw.Draw(im)
    draw.rectangle((35, 32, 685, 445), outline=(32, 58, 67), width=3)
    draw.text((64, 64), "SAMPLE / Scan 002", fill=(22, 36, 41))
    for row in range(9):
        y = 110 + row * 33
        draw.text((65, y), f"Record {row + 1:02d}       {row * 173 + 500:,}", fill=(26, 36, 38))
        draw.line((65, y + 19, 610, y + 19), fill=(120, 136, 136))
    draw.ellipse((585, 326, 648, 389), outline=(162, 50, 40), width=4)
    image_data = BytesIO()
    im.save(image_data, format="JPEG", quality=78, optimize=True, dpi=(150, 150))
    stream = pdf.make_stream(image_data.getvalue())
    for key, value in Dictionary(Type=Name.XObject, Subtype=Name.Image, Width=720,
                                 Height=480, ColorSpace=Name.DeviceRGB, BitsPerComponent=8,
                                 Filter=Name.DCTDecode).items():
        stream[key] = value
    return stream


def make_sample(path: Path, *, precompressed=False, version="1.7", needs_appearances=False):
    with pikepdf.Pdf.new() as pdf:
        latin = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1,
                                             BaseFont=Name.Helvetica))
        fonts = Dictionary(F1=latin, K=korean_font(pdf))
        first = pdf.add_blank_page(page_size=(420, 560))
        first.Resources = Dictionary(Font=fonts)
        private = Dictionary(Contents="ordinary private text", ByteRange=Array([0, 6, 12, 20]),
                             Template=Dictionary(FT=Name.Sig, V=Dictionary(Contents="template memo")))
        first.obj.PieceInfo = Dictionary(Example=Dictionary(LastModified="D:20250101000000Z",
                                                            Private=private))
        content = [b"0.1 0.2 0.24 rg BT /K 24 Tf 30 512 Td <0102> Tj ET",
                   b"BT /F1 16 Tf 88 516 Td (Sample register) Tj ET"]
        for row in range(20):
            y = 480 - row * 20
            content.append((f"0.65 G 0.5 w 30 {y-5} m 388 {y-5} l S "
                            f"0.1 g BT /F1 9 Tf 36 {y} Td (Item {row+1:02d}) Tj "
                            f"170 0 Td ({row * 137 + 314:,}) Tj ET").encode())
        first.Contents = pdf.make_stream(b"\n".join(content))
        link = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link,
                                            Rect=Array([28, 505, 200, 541]),
                                            Border=Array([0, 0, 0]), Dest=Array([first.obj, Name.Fit])))
        field = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Widget, FT=Name.Tx,
                                             T="Example field", V="42", Rect=Array([30, 34, 75, 54]),
                                             F=4, P=first.obj, DA="/F1 9 Tf 0 g"))
        appearance = pdf.make_stream(b"q 0.8 G 0 0 45 20 re S BT /F1 9 Tf 3 6 Td (42) Tj ET Q")
        appearance.Type, appearance.Subtype = Name.XObject, Name.Form
        appearance.BBox, appearance.Resources = Array([0, 0, 45, 20]), Dictionary(Font=fonts)
        field.AP = Dictionary(N=appearance)
        first.Annots = Array([link, field])
        pdf.Root.AcroForm = Dictionary(Fields=Array([field]), DR=Dictionary(Font=fonts),
                                       DA="/F1 9 Tf 0 g")
        if needs_appearances:
            pdf.Root.AcroForm.NeedAppearances = True
        second = pdf.add_blank_page(page_size=(420, 560))
        second.Resources = Dictionary(Font=fonts, XObject=Dictionary(Scan=scan_image(pdf)))
        second.Contents = pdf.make_stream(
            b"q 384 0 0 256 18 250 cm /Scan Do Q\n"
            b"BT /F1 10 Tf 3 Tr 48 470 Td (Searchable scan 002) Tj ET")
        pdf.attachments["sample.txt"] = b"Local sample attachment.\n"
        with pdf.open_outline() as outline:
            outline.root.append(pikepdf.OutlineItem("Sample register", 0))
        pdf.docinfo["/Title"] = "한글 PDF 견본"
        if precompressed:
            pikepdf.settings.set_flate_compression_level(1)
        pdf.save(path, min_version=version, compress_streams=precompressed,
                 object_stream_mode=pikepdf.ObjectStreamMode.disable)


def render_snapshot(path):
    import pypdfium2 as pdfium

    snapshot = []
    with pdfium.PdfDocument(path) as doc:
        doc.init_forms()
        for page in doc:
            text = page.get_textpage()
            bitmap = page.render(scale=2, draw_annots=True)
            pixels = bitmap.to_pil().convert("RGB")
            snapshot.append((pixels.size, pixels.tobytes(), text.get_text_bounded()))
            bitmap.close()
            text.close()
            page.close()
    return snapshot
