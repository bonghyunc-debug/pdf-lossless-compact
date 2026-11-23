"""Generate and verify a PDF signature with an ephemeral test-only certificate."""

from datetime import datetime, timedelta, timezone
from io import BytesIO

from asn1crypto import keys, x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import fields, signers
from pyhanko.sign.validation import validate_pdf_signature
from pyhanko.stamp import TextStampStyle
from pyhanko_certvalidator import ValidationContext
from pyhanko_certvalidator.registry import SimpleCertificateStore

from tests.samples import make_sample


class _ViewerGeneratedFormWriter(IncrementalPdfFileWriter):
    def write(self, stream):
        # pyHanko normally clears this flag. Set it before hashing each signed
        # revision to model PDFs from producers that leave it enabled.
        form = self.root["/AcroForm"]
        form[generic.pdf_name("/NeedAppearances")] = generic.BooleanObject(True)
        self.update_container(form)
        super().write(stream)


def _make_signer(common_name):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True,
                           key_encipherment=False, data_encipherment=False, key_agreement=False,
                           key_cert_sign=False, crl_sign=False, encipher_only=None,
                           decipher_only=None), critical=True)
            .sign(key, hashes.SHA256()))
    return signers.SimpleSigner(
        signing_cert=asn1_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER)),
        signing_key=keys.PrivateKeyInfo.load(key.private_bytes(
            serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption())),
        cert_registry=SimpleCertificateStore())


def make_signed_sample(path, *, separate_widget=False, visible=True, two_signers=False,
                       needs_appearances=False):
    base = BytesIO()
    make_sample(base, precompressed=True, needs_appearances=needs_appearances)
    base.seek(0)
    writer_type = _ViewerGeneratedFormWriter if needs_appearances else IncrementalPdfFileWriter
    writer = writer_type(base)
    fields.append_signature_field(writer, fields.SigFieldSpec("Unused", on_page=1))
    if two_signers:
        fields.append_signature_field(writer, fields.SigFieldSpec("Review", on_page=1,
                                      box=(210, 32, 392, 82)))
    signer = _make_signer("PDF Compact Test A")
    meta = signers.PdfSignatureMetadata(field_name="Approval", md_algorithm="sha256", certify=True)
    box = (210, 32, 392, 82) if visible else None
    field = fields.SigFieldSpec("Approval", on_page=0, box=box, combine_annotation=not separate_widget)
    stamp = TextStampStyle(stamp_text="TEST SIGNATURE", background=None, border_width=2)
    first = signers.PdfSigner(meta, signer=signer, new_field_spec=field,
                              stamp_style=stamp).sign_pdf(writer)
    certificates = [signer.signing_cert]
    if two_signers:
        other = _make_signer("PDF Compact Test B")
        certificates.append(other.signing_cert)
        writer = writer_type(first)
        meta = signers.PdfSignatureMetadata(field_name="Review", md_algorithm="sha256")
        first = signers.PdfSigner(meta, signer=other, stamp_style=stamp).sign_pdf(writer)
    path.write_bytes(first.getvalue())
    return certificates


def verify_original(path, certificates):
    with path.open("rb") as stream:
        signatures = PdfFileReader(stream).embedded_signatures
        assert len(signatures) == len(certificates)
        context = ValidationContext(trust_roots=certificates, allow_fetching=False)
        for signature in signatures:
            status = validate_pdf_signature(signature, signer_validation_context=context)
            assert status.bottom_line and status.intact and status.valid and status.trusted, status.summary()
