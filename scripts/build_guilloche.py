"""Draw the security-print line work the UI uses: web/rosette.svg and web/band.svg. Only math, no source artwork.

    python scripts/build_guilloche.py
"""
import math
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


def ring(radius, swing, waves, lines, per_wave=8, cx=1000, cy=1000):
    """Closed wavy lines around a circle, each shifted a little: the crossing lines of a banknote rosette."""
    out = []
    for k in range(lines):
        shift = 2 * math.pi * k / lines
        n, cmds, x0, y0 = waves * per_wave, [], None, None
        for i in range(n):
            t = 2 * math.pi * i / n
            r = radius + swing * math.sin(waves * t + shift) * (0.72 + 0.28 * math.cos(waves * t / 2 + shift))
            x, y = round(cx + r * math.cos(t)), round(cy + r * math.sin(t))
            cmds.append(f"M{x} {y}" if i == 0 else f"l{x - x0} {y - y0}")
            x0, y0 = x, y
        out.append("".join(cmds) + "z")
    return "".join(out)


def rosette():
    d = ring(780, 190, 20, 14) + ring(450, 130, 14, 12) + ring(190, 90, 8, 10)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2000 2000">'
            f'<path d="{d}" fill="none" stroke="#000" stroke-width="2.4"/></svg>\n')


def band(width=240, height=48, lines=7):
    paths = []
    for k in range(lines):
        pts = []
        for x in range(0, width + 1, 8):
            y = height / 2 + (height / 2 - 2) * math.sin(2 * math.pi * x / width * 2 + 2 * math.pi * k / lines)
            pts.append(f"{x} {y:.1f}")
        paths.append("M" + "L".join(pts))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">'
            f'<path d="{"".join(paths)}" fill="none" stroke="#000" stroke-width="1"/></svg>\n')


if __name__ == "__main__":
    (WEB / "rosette.svg").write_text(rosette(), encoding="utf-8")
    (WEB / "band.svg").write_text(band(), encoding="utf-8")
