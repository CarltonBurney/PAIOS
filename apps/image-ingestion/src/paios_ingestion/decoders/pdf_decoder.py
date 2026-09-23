"""PDF adapter: one normalized page per source page, rendered by PDFium at the configured DPI."""
from __future__ import annotations

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

from ..errors import failure
from ..normalize import check_deadline, pdf_pixel_size, to_srgb_rgb, write_page
from .base import BaseDecoder

_FPDF_ERR_PASSWORD = 4


class PdfDecoder(BaseDecoder):
    decoder_id = "pdfium"
    decoder_version = (f"pypdfium2-{pdfium.version.PYPDFIUM_INFO}"
                       f"+pdfium-{pdfium.version.PDFIUM_INFO}")
    mime_types = frozenset({"application/pdf"})

    def _decode(self, source, mime, output_dir, limits, budget, written):
        try:
            document = pdfium.PdfDocument(str(source))
        except pdfium.PdfiumError as exc:
            if getattr(exc, "err_code", None) == _FPDF_ERR_PASSWORD:
                raise failure("ENCRYPTED_MEDIA", "decode", "PDF requires a password") from None
            raise failure("DECODE_FAILED", "decode", "PDF could not be opened",
                          details=[("source", f"pdfium error {getattr(exc, 'err_code', 'unknown')}")]
                          ) from None
        try:
            # Any security handler means encrypted, including owner-password-only files
            # that open without a password. v1 rejects all of them.
            if pdfium_raw.FPDF_GetSecurityHandlerRevision(document.raw) != -1:
                raise failure("ENCRYPTED_MEDIA", "decode", "Encrypted PDFs are not supported")
            dpi = limits.pdf_dpi
            count = len(document)
            budget.check_page_count(count)
            for index in range(count):  # check every page before rendering any
                page = document[index]
                try:
                    budget.admit(index + 1, *pdf_pixel_size(*page.get_size(), dpi))
                finally:
                    page.close()
            document.init_forms()
            pages, notes = [], []
            for index in range(count):
                check_deadline(limits)
                page = document[index]
                try:
                    bitmap = page.render(scale=dpi / 72, may_draw_forms=True)
                    image = bitmap.to_pil()
                finally:
                    page.close()
                rgb, note = to_srgb_rgb(image)
                local = write_page(rgb, output_dir, index + 1, dpi)
                written.append(local.path)
                pages.append(local)
                notes.append({"page_number": index + 1, **note})
            return pages, "PDF", notes
        finally:
            document.close()
