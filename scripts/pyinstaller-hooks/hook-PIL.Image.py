# The sidecar uses Pillow only to decode images before sending them to
# Tesseract. Keep the two formats that Telegram OCR accepts explicitly and do
# not pull every optional Pillow image plugin into the frozen helper.
hiddenimports = [
    "PIL.JpegImagePlugin",
    "PIL.PngImagePlugin",
]
