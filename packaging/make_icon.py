"""Draw the app icon with AppKit and write packaging/AppIcon.icns.

Two index cards — 中 and EN — tilted against each other on a deep teal tile.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import AppKit
import Foundation

HERE = Path(__file__).resolve().parent


def rgb(hex_color: str, alpha: float = 1.0) -> AppKit.NSColor:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, alpha)


def card(ctx_size: float, center: tuple[float, float], angle: float, fill: AppKit.NSColor, text: str, text_color: AppKit.NSColor, font_size: float) -> None:
    w, h = ctx_size * 0.30, ctx_size * 0.38
    transform = AppKit.NSAffineTransform.transform()
    transform.translateXBy_yBy_(*center)
    transform.rotateByDegrees_(angle)
    AppKit.NSGraphicsContext.saveGraphicsState()
    transform.concat()

    shadow = AppKit.NSShadow.alloc().init()
    shadow.setShadowOffset_(Foundation.NSMakeSize(0, -ctx_size * 0.012))
    shadow.setShadowBlurRadius_(ctx_size * 0.03)
    shadow.setShadowColor_(rgb("#000000", 0.28))
    shadow.set()

    rect = Foundation.NSMakeRect(-w / 2, -h / 2, w, h)
    path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, ctx_size * 0.04, ctx_size * 0.04)
    fill.setFill()
    path.fill()
    AppKit.NSShadow.alloc().init().set()

    font = AppKit.NSFont.systemFontOfSize_weight_(font_size, AppKit.NSFontWeightBold)
    attrs = {AppKit.NSFontAttributeName: font, AppKit.NSForegroundColorAttributeName: text_color}
    string = Foundation.NSAttributedString.alloc().initWithString_attributes_(text, attrs)
    size = string.size()
    string.drawAtPoint_(Foundation.NSMakePoint(-size.width / 2, -size.height / 2))
    AppKit.NSGraphicsContext.restoreGraphicsState()


def draw(size: int) -> AppKit.NSBitmapImageRep:
    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, size, size, 8, 4, True, False, AppKit.NSDeviceRGBColorSpace, 0, 0
    )
    AppKit.NSGraphicsContext.saveGraphicsState()
    AppKit.NSGraphicsContext.setCurrentContext_(AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))

    inset = size * 0.1
    tile = Foundation.NSMakeRect(inset, inset, size - 2 * inset, size - 2 * inset)
    tile_path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(tile, size * 0.18, size * 0.18)
    gradient = AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(rgb("#14937D"), rgb("#0A5F52"))
    gradient.drawInBezierPath_angle_(tile_path, -70)

    card(size, (size * 0.365, size * 0.585), 10, rgb("#151C29"), "中", rgb("#FFFFFF"), size * 0.2)
    card(size, (size * 0.635, size * 0.425), -8, rgb("#FFFFFF"), "EN", rgb("#0C7A68"), size * 0.14)

    AppKit.NSGraphicsContext.restoreGraphicsState()
    return rep


def main() -> None:
    iconset = Path(tempfile.mkdtemp()) / "AppIcon.iconset"
    iconset.mkdir()
    for base in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = base * scale
            name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
            data = draw(px).representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
            data.writeToFile_atomically_(str(iconset / name), True)
    target = HERE / "AppIcon.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(target)], check=True)
    shutil.rmtree(iconset.parent)
    print(target)


if __name__ == "__main__":
    main()
