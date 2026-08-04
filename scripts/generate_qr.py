"""Generate the single global QR code that opens the lab assistant (R4).

Usage: python scripts/generate_qr.py [URL]
Defaults to BASE_URL from settings. Writes app/static/img/lab_qr.png.
"""
import sys

import qrcode

from app.config import settings


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else settings.base_url
    img = qrcode.make(url)
    out = "app/static/img/lab_qr.png"
    img.save(out)
    print(f"QR for {url} -> {out}")


if __name__ == "__main__":
    main()
