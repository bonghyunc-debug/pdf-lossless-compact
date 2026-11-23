from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image
import pikepdf
from pikepdf import Array, Dictionary, Name
from pyhanko.pdf_utils.reader import PdfFileReader

from compress import PdfSkipped, compress
from tests.samples import make_sample, render_snapshot
from tests.signed_samples import make_signed_sample, verify_original


class SignedCopyTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.source = self.folder / "서명 견본.pdf"

    def test_valid_signatures_and_visible_appearances(self):
        for separate, visible, two, needs_ap in ((False, True, False, False), (True, True, True, False),
                                                 (True, False, False, False), (True, True, True, True)):
            with self.subTest(separate_widget=separate, visible=visible, two_signers=two,
                              needs_appearances=needs_ap):
                certs = make_signed_sample(self.source, separate_widget=separate,
                                           visible=visible, two_signers=two, needs_appearances=needs_ap)
                verify_original(self.source, certs)
                original = self.source.read_bytes()
                before = render_snapshot(self.source)
                result = compress(self.source)
                self.assertTrue(result.reading_copy)
                self.assertIsNotNone(result.output)
                self.assertIn("_열람용_압축", result.output.stem)
                self.assertLess(result.candidate_size, len(original))
                self.assertEqual(self.source.read_bytes(), original)
                verify_original(self.source, certs)
                with result.output.open("rb") as stream:
                    self.assertEqual(PdfFileReader(stream).embedded_signatures, [])
                after = render_snapshot(result.output)
                self.assertEqual(before, after)
                if visible:
                    for page in before[:len(certs)]:
                        size, pixels, _ = page
                        ink = Image.frombytes("RGB", size, pixels).crop((420, 956, 784, 1056)).convert("L")
                        self.assertGreater(sum(ink.histogram()[:160]), 300)
                with pikepdf.Pdf.open(result.output) as pdf:
                    fields = {f.fully_qualified_name: f for f in pdf.acroform.fields}
                    self.assertEqual(fields["Unused"].field_type, "/Sig")
                    self.assertIsNone(fields["Unused"].value)
                    self.assertFalse(fields["Unused"].flags & pikepdf.FormFieldFlag.read_only)
                    self.assertEqual(fields["Example field"].value, "42")
                    self.assertEqual(fields["Approval"].field_type, "/Sig")
                    self.assertIsNone(fields["Approval"].value)
                    self.assertTrue(fields["Approval"].flags & pikepdf.FormFieldFlag.read_only)
                    self.assertEqual(bool(pdf.Root.AcroForm.get("/NeedAppearances")), needs_ap)
                    self.assertEqual(pdf.Root.AcroForm.SigFlags, 1)
                    self.assertNotIn("/Perms", pdf.Root)
                    private = pdf.pages[0].obj.PieceInfo.Example.Private
                    self.assertEqual(private.Contents, "ordinary private text")
                    self.assertEqual(list(private.ByteRange), [0, 6, 12, 20])
                    self.assertEqual(private.Template.V.Contents, "template memo")
                    self.assertEqual(pdf.attachments["sample.txt"].get_file().read_bytes(),
                                     b"Local sample attachment.\n")
                print(f"Signed PDF: {len(certs)} signature(s), visible={visible}, NeedAppearances={needs_ap}, "
                      f"{len(original)} -> {result.candidate_size} bytes; original signatures verified; "
                      "copy has zero signatures; page pixels and text matched.")

    def test_signed_output_does_not_overwrite_other_copies(self):
        make_signed_sample(self.source)
        existing = self.folder / "서명 견본_열람용_압축.pdf"
        existing.write_bytes(b"previous file")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(compress, [self.source, self.source]))
        self.assertEqual({r.output.name for r in results},
                         {"서명 견본_열람용_압축 (2).pdf", "서명 견본_열람용_압축 (3).pdf"})
        self.assertEqual(existing.read_bytes(), b"previous file")

    def test_visible_widget_missing_appearance_does_not_produce_incomplete_copy(self):
        signed = self.folder / "source.pdf"
        make_signed_sample(signed)
        with pikepdf.Pdf.open(signed) as pdf:
            widget = next(n for n in pdf.pages[0].Annots if n.get("/FT") == Name.Sig)
            del widget["/AP"]
            pdf.save(self.source)
        before = self.source.read_bytes()
        with self.assertRaisesRegex(PdfSkipped, "표시 정보"):
            compress(self.source)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(set(self.folder.iterdir()), {self.source, signed})

    def test_page_content_with_unrelated_byte_range_is_preserved(self):
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page()
            page.Contents = pdf.make_stream(b"0.4 g 10 10 20 20 re f\n" * 80)
            page.obj["/ByteRange"] = Array([0, 20])
            page.Resources.Example = Dictionary(Contents="ordinary", ByteRange=Array([0, 6, 12, 20]))
            template = Dictionary(FT=Name.Sig, V=Dictionary(Contents="template memo"))
            page.obj.PieceInfo = Dictionary(Example=Dictionary(LastModified="D:20250101000000Z",
                                                               Private=template))
            pdf.save(self.source, compress_streams=False)
        result = compress(self.source)
        self.assertFalse(result.reading_copy)
        self.assertEqual(render_snapshot(result.output), render_snapshot(self.source))
        with pikepdf.Pdf.open(result.output) as pdf:
            data = pdf.pages[0].Resources.Example
            self.assertEqual(data.Contents, "ordinary")
            self.assertEqual(list(data.ByteRange), [0, 6, 12, 20])
            template = pdf.pages[0].obj.PieceInfo.Example.Private
            self.assertEqual(template.FT, Name.Sig)
            self.assertEqual(template.V.Contents, "template memo")

    def test_untyped_signature_field_and_usage_rights_keep_other_form_data(self):
        base = self.folder / "base.pdf"
        make_sample(base)
        for location in ("field", "separate-widget", "/UR", "/UR3"):
            with self.subTest(location=location):
                with pikepdf.Pdf.open(base) as pdf:
                    value = pdf.make_indirect(Dictionary(
                        Filter=Name("/Adobe.PPKLite"), Contents=b"test marker" * 400,
                        ByteRange=Array([0, 12, 16, 8])))
                    pdf.Root.ExampleAlias = value
                    pdf.Root.Perms = Dictionary(ExamplePermission="keep")
                    if location == "field":
                        pdf.Root.AcroForm.Fields.append(
                            pdf.make_indirect(Dictionary(FT=Name.Sig, T="Archived", V=value)))
                    elif location == "separate-widget":
                        parent = pdf.make_indirect(Dictionary(FT=Name.Sig, T="Archived", V=value))
                        widget = pdf.make_indirect(Dictionary(
                            Type=Name.Annot, Subtype=Name.Widget, Parent=parent, Rect=Array([0, 0, 0, 0])))
                        parent.Kids = Array([widget])
                        pdf.Root.AcroForm.Fields.append(parent)
                        pdf.pages[0].Annots.append(widget)
                    else:
                        pdf.Root.Perms[location] = value
                    pdf.save(self.source, compress_streams=False)
                before = self.source.read_bytes()
                result = compress(self.source)
                self.assertTrue(result.reading_copy)
                self.assertEqual(self.source.read_bytes(), before)
                self.assertEqual(render_snapshot(self.source), render_snapshot(result.output))
                with pikepdf.Pdf.open(result.output) as copy:
                    self.assertNotIn("/Contents", copy.Root.ExampleAlias)
                    self.assertNotIn("/ByteRange", copy.Root.ExampleAlias)
                    self.assertEqual(set(copy.Root.Perms.keys()), {"/ExamplePermission"})
                    self.assertEqual(copy.Root.Perms.ExamplePermission, "keep")
                    self.assertEqual(copy.Root.AcroForm.Fields[0].V, "42")
