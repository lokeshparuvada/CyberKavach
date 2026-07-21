"""
download_fonts.py
------------------
One-time helper that fetches the Noto Sans regional-script TTF files
`reporting/pdf_generator.py` looks for at runtime, and saves them into
this folder (reporting/fonts/) with the exact filenames it expects.

Run this from a machine with normal internet access -- the sandbox that
built the rest of this project has none, which is why the files aren't
already here. Once they exist, the PDF generator picks them up
automatically; no code changes needed.

Usage:
    python download_fonts.py            # fetches all 6 wired-up scripts
    python download_fonts.py ta hi      # fetches only Tamil + Hindi

How it works: Google Fonts serves .ttf (instead of .woff2) to older/
unrecognized browsers -- this is the standard trick for scripting Google
Fonts downloads without needing an API key. If Google changes their CSS
response format and this script stops finding URLs, fall back to the
manual method described in RUN_INSTRUCTIONS.md.
"""
import re
import sys
import urllib.request
from pathlib import Path

FONT_DIR = Path(__file__).parent  # reporting/fonts/

# language code -> (Google Fonts family name, regular filename, bold filename or None)
FAMILIES = {
    "ta": ("Noto Sans Tamil", "NotoSansTamil-Regular.ttf", "NotoSansTamil-Bold.ttf"),
    "hi": ("Noto Sans Devanagari", "NotoSansDevanagari-Regular.ttf", None),
    "te": ("Noto Sans Telugu", "NotoSansTelugu-Regular.ttf", None),
    "kn": ("Noto Sans Kannada", "NotoSansKannada-Regular.ttf", None),
    "ml": ("Noto Sans Malayalam", "NotoSansMalayalam-Regular.ttf", None),
    "bn": ("Noto Sans Bengali", "NotoSansBengali-Regular.ttf", None),
}

LEGACY_UA = (
    "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/30.0.0.0 Safari/537.36"
)


def _fetch_css(family: str) -> str:
    url = (
        "https://fonts.googleapis.com/css2?family="
        + family.replace(" ", "+")
        + ":wght@400;700&display=swap"
    )
    req = urllib.request.Request(url, headers={"User-Agent": LEGACY_UA})
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode("utf-8")


def _ttf_urls(css: str) -> list[str]:
    return re.findall(r"url\((https://fonts\.gstatic\.com/[^)]+\.ttf)\)", css)


def _download(url: str, dest: Path):
    req = urllib.request.Request(url, headers={"User-Agent": LEGACY_UA})
    with urllib.request.urlopen(req) as resp:
        dest.write_bytes(resp.read())
    print(f"  saved {dest.name} ({dest.stat().st_size // 1024} KB)")


def main():
    langs = sys.argv[1:] or list(FAMILIES.keys())
    FONT_DIR.mkdir(exist_ok=True)
    for lang in langs:
        if lang not in FAMILIES:
            print(f"Skipping unknown language code '{lang}' (known: {list(FAMILIES)})")
            continue
        family, regular_name, bold_name = FAMILIES[lang]
        print(f"Fetching {family} ({lang})...")
        try:
            css = _fetch_css(family)
        except Exception as e:
            print(f"  Failed to reach Google Fonts: {e}")
            continue
        urls = _ttf_urls(css)
        if not urls:
            print(f"  No .ttf URLs found for {family} -- Google may have changed "
                  f"their CSS response format. Use the manual download method instead.")
            continue
        # First URL returned is typically weight 400 (regular); the last is
        # usually the heaviest weight requested (700/bold).
        _download(urls[0], FONT_DIR / regular_name)
        if bold_name and len(urls) > 1:
            _download(urls[-1], FONT_DIR / bold_name)

    print("\nDone. Re-run your PDF generation -- reporting/pdf_generator.py "
          "will pick these up automatically.")


if __name__ == "__main__":
    main()