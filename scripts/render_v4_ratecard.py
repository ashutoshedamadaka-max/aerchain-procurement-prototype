#!/usr/bin/env python3
"""Render V4's clean rate card from truth, then degrade it deterministically (fixed seed).

Writes dataset/artifacts/V4_Meridian_RateCard_clean.png and V4_Meridian_RateCard.jpg,
and the seed, angles and warp matrix to dataset/truth/v4_degradation.json.
Run: python scripts/render_v4_ratecard.py [--level 1.0] [--glare 0.85]
  level scales the global warp/light/blur; glare is the peak whiteout of the specular ellipse (rows 11-14).
Localized damage (glare rows 11-14, crease rows 22-23, occlusion on the leading digit of row 27) is recorded in
v4_degradation.json as expected_uncertain.
"""
import argparse
import json
import pathlib
import sqlite3

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[1]
ART, TRUTH = ROOT / "dataset" / "artifacts", ROOT / "dataset" / "truth"
FONTS = pathlib.Path("C:/Windows/Fonts")
SEED = 4242
W = 1800
ROW_H, HEAD_H, TOP_H = 66, 74, 400
COLS = [("Size (mm)", 90, 420), ("Ply / GSM", 510, 300), ("Print", 810, 220), ("Rate (USD)", 1030, 260), ("Unit", 1290, 200)]
NAVY, PAPER, GRID = (24, 52, 96), (250, 249, 244), (150, 155, 165)


def font(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


def load_rows():
    con = sqlite3.connect(TRUTH / "truth.sqlite")
    lines = con.execute("SELECT line_no,length_mm,width_mm,height_mm,ply,liner_gsm,print_spec FROM rfx_lines ORDER BY line_no").fetchall()
    price = {n: (v, u) for n, v, u in con.execute(
        "SELECT rfx_line_no,value,unit FROM bid_fields b JOIN submissions s USING(submission_id) "
        "WHERE s.vendor_id='V4' AND field_name='unit_price' ORDER BY rfx_line_no")}
    foot = [s for (s,) in con.execute("SELECT snippet FROM conditions c JOIN submissions s USING(submission_id) "
                                      "WHERE s.vendor_id='V4' AND anchor='ratecard:footer' ORDER BY condition_id")]
    return lines, price, foot


def render_clean():
    lines, price, foot = load_rows()
    H = TOP_H + HEAD_H + ROW_H * len(lines) + 60 + 70 * (len(foot) + 1)
    im = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(im)
    # letterhead
    d.rounded_rectangle((90, 60, 230, 200), 22, fill=NAVY)
    d.text((160, 130), "M", font=font("arialbd.ttf", 96), fill="white", anchor="mm")
    d.text((270, 62), "MERIDIAN PACK TRADING CO.", font=font("arialbd.ttf", 62), fill=NAVY)
    d.text((272, 140), "Corrugated cartons  |  Domestic & export supply", font=font("ariali.ttf", 32), fill=(70, 70, 80))
    d.text((272, 190), "Unit 14, Vashi Industrial Estate, Navi Mumbai 400703  |  GSTIN 27AAKFM4471C1Z6", font=font("arial.ttf", 26), fill=(70, 70, 80))
    d.line((90, 250, W - 90, 250), fill=NAVY, width=5)
    d.text((90, 285), "PRICE LIST - REGULAR SLOTTED CARTONS", font=font("arialbd.ttf", 44), fill=(20, 20, 20))
    d.text((90, 345), "Date: 07 March 2026    Ref: MPT/PL/0307    Prices in US Dollars, EXW Navi Mumbai",
           font=font("arial.ttf", 30), fill=(60, 60, 60))
    # table
    y = TOP_H
    d.rectangle((90, y, W - 90, y + HEAD_H), fill=NAVY)
    f_head, f_row = font("arialbd.ttf", 32), font("calibrib.ttf", 34)
    d.text((105, y + HEAD_H // 2), "No.", font=f_head, fill="white", anchor="lm")
    for name, x, wd in COLS:
        d.text((x + 150 if name == "Size (mm)" else x + 10, y + HEAD_H // 2), name, font=f_head, fill="white", anchor="lm")
    y += HEAD_H
    for n, L, Wd, Ht, ply, gsm, prt in lines:
        val, unit = price[n]
        if n % 2 == 0:
            d.rectangle((90, y, W - 90, y + ROW_H), fill=(236, 239, 245))
        cells = [f"{L} x {Wd} x {Ht}", f"{ply} ply {gsm} GSM", "Plain" if prt == "plain" else prt.replace("-colour flexo", " colour"),
                 f"{val:.3f}", "per kg" if unit == "USD/kg" else "per pc"]
        d.text((105, y + ROW_H // 2), f"{n}", font=f_row, fill=(60, 60, 60), anchor="lm")
        for (name, x, wd), t in zip(COLS, cells):
            d.text((x + 150 if name == "Size (mm)" else x + 10, y + ROW_H // 2), t, font=f_row, fill=(15, 15, 15), anchor="lm")
        d.line((90, y + ROW_H, W - 90, y + ROW_H), fill=GRID, width=1)
        y += ROW_H
    d.rectangle((90, TOP_H, W - 90, y), outline=NAVY, width=3)
    for _, x, _ in COLS:
        d.line((x - 8 if x > 90 else x, TOP_H + HEAD_H, x - 8 if x > 90 else x, y), fill=GRID, width=1)
    # footer
    y += 45
    for t in ["MOQ 5,000 per size"] + [s for s in foot if not s.startswith("MOQ")]:
        d.text((90, y), t, font=font("arialbd.ttf" if t.startswith("MOQ") else "arial.ttf", 38 if t.startswith("MOQ") else 30), fill=(20, 20, 20))
        y += 70
    return im


ROW0 = TOP_H + HEAD_H
RATE_X = COLS[3][1] + 10                       # left edge of the rate text in the clean image


def row_y(n):
    return ROW0 + (n - 1) * ROW_H              # top of row n in clean px


def smooth(x):
    x = np.clip(x, 0, 1)
    return x * x * (3 - 2 * x)


def apply_damage(img, X, Y, glare):
    """X, Y: clean-card coordinates of every canvas pixel, so the damage sits on the card and follows the warp."""
    # specular glare ellipse washing out the rate column across rows 11-14
    gx, gy = COLS[3][1] + COLS[3][2] / 2, (row_y(11) + row_y(15)) / 2
    g = smooth((1 - np.sqrt(((X - gx) / 250) ** 2 + ((Y - gy) / 195) ** 2)) / 0.4)
    img = img + (255 - img) * (glare * g)[..., None]
    # crease: diagonal through rows 22-23 with a brightness step across it, a dark fold line and a blocky compressed band
    ang = np.radians(58)
    ux, uy, nx, ny = np.cos(ang), np.sin(ang), -np.sin(ang), np.cos(ang)
    px, py, half = RATE_X + 45, row_y(23), 92
    d = (X - px) * nx + (Y - py) * ny
    seg = np.clip((half - np.abs((X - px) * ux + (Y - py) * uy)) / 14, 0, 1)
    img = img + (16 * np.tanh(d / 2.0) * np.exp(-(d / 28) ** 2) * seg)[..., None]
    img = img * (1 - 0.30 * np.exp(-(d / 2.2) ** 2) * seg)[..., None]
    ch, cw = img.shape[:2]
    mosaic = cv2.resize(cv2.resize(img, (cw // 4, ch // 4), interpolation=cv2.INTER_AREA), (cw, ch), interpolation=cv2.INTER_NEAREST)
    mix = (smooth((16 - np.abs(d)) / 6) * seg)[..., None]
    img = img * (1 - mix) + mosaic * mix
    # soft dark occlusion clipping the leading digit of the rate on row 27
    o = smooth((1 - np.sqrt(((X - (RATE_X - 5)) / 40) ** 2 + ((Y - (row_y(27) + ROW_H / 2)) / 46) ** 2)) / 1.0)
    return img * (1 - 0.85 * o)[..., None]


def degrade(clean, level, glare=0.85):
    rs = np.random.RandomState(SEED)
    a_y, a_x, a_z = rs.uniform(10, 15) * level, rs.uniform(4, 7) * level, rs.uniform(1.5, 3) * level   # degrees
    blur_len, blur_ang = max(1, int(round(5 * level))), rs.uniform(-15, 15)
    src = cv2.cvtColor(np.asarray(clean), cv2.COLOR_RGB2BGR)
    h, w = src.shape[:2]
    ay, ax, az = np.radians([a_y, a_x, a_z])
    Ry = np.array([[np.cos(ay), 0, np.sin(ay)], [0, 1, 0], [-np.sin(ay), 0, np.cos(ay)]])
    Rx = np.array([[1, 0, 0], [0, np.cos(ax), -np.sin(ax)], [0, np.sin(ax), np.cos(ax)]])
    Rz = np.array([[np.cos(az), -np.sin(az), 0], [np.sin(az), np.cos(az), 0], [0, 0, 1]])
    R, dist = Rz @ Ry @ Rx, 2.2 * w
    box = np.array([[-w / 2, -h / 2, 0], [w / 2, -h / 2, 0], [w / 2, h / 2, 0], [-w / 2, h / 2, 0]]) @ R.T
    pts = np.stack([box[:, 0] * dist / (dist + box[:, 2]), box[:, 1] * dist / (dist + box[:, 2])], 1)
    margin = 120
    pts -= pts.min(0) - margin
    cw, ch = (pts.max(0) + margin).astype(int)
    M = cv2.getPerspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]), np.float32(pts))
    # desk background: smooth warm-grey noise, drawn first so the warped card sits on it
    desk = cv2.GaussianBlur(rs.normal(0, 1, (ch // 8, cw // 8)).astype(np.float32), (0, 0), 6)
    desk = cv2.resize(desk, (cw, ch)) * 9 + 92
    canvas = np.dstack([desk * 0.80, desk * 0.90, desk * 1.00]).clip(0, 255).astype(np.uint8)
    cv2.warpPerspective(src, M, (cw, ch), dst=canvas, flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_TRANSPARENT)
    # overhead light (bright top centre, falling off) plus one shadowed corner (bottom-left)
    yy, xx = np.mgrid[0:ch, 0:cw].astype(np.float32)
    light = 1.10 - 0.30 * np.sqrt(((xx - cw / 2) / cw) ** 2 * 0.8 + (yy / ch) ** 2 * 1.1)
    shadow = np.clip(1 - np.hypot(xx / cw * 1.0, (ch - yy) / ch * 1.3) / 0.62, 0, 1)
    shadow = 1 - 0.42 * level * shadow ** 1.5
    img = (canvas.astype(np.float32) * (1 + (light - 1) * level)[..., None] * shadow[..., None]).clip(0, 255)
    gxy = np.mgrid[0:h, 0:w].astype(np.float32)
    X, Y = (cv2.warpPerspective(gxy[i], M, (cw, ch), flags=cv2.INTER_LINEAR, borderValue=-1e4) for i in (1, 0))
    img = apply_damage(img, X, Y, glare).clip(0, 255)
    # slight motion blur
    k = np.zeros((blur_len, blur_len), np.float32)
    k[blur_len // 2, :] = 1
    rot = cv2.getRotationMatrix2D((blur_len / 2 - 0.5, blur_len / 2 - 0.5), blur_ang, 1)
    k = cv2.warpAffine(k, rot, (blur_len, blur_len))
    img = cv2.filter2D(img, -1, k / k.sum()) if blur_len > 1 else img
    out = Image.fromarray(cv2.cvtColor(img.astype(np.uint8), cv2.COLOR_BGR2RGB))
    return out, dict(seed=SEED, level=level, angles_deg=dict(yaw=a_y, pitch=a_x, roll=a_z), blur_len=blur_len, blur_angle_deg=blur_ang,
                     jpeg_quality=70, perspective_matrix=M.tolist(), canvas_px=[int(cw), int(ch)], clean_px=[w, h],
                     glare_intensity=glare,
                     expected_uncertain=[11, 12, 13, 14, 22, 23, 27],
                     expected_uncertain_causes={"11-14": "specular glare over the rate column", "22-23": "diagonal crease through the rate digits",
                                                "27": "soft occlusion over the leading digit of the rate"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=float, default=1.0)
    ap.add_argument("--glare", type=float, default=0.85)
    args = ap.parse_args()
    level = args.level
    ART.mkdir(parents=True, exist_ok=True)
    clean = render_clean()
    clean.save(ART / "V4_Meridian_RateCard_clean.png")
    out, meta = degrade(clean, level, args.glare)
    out.save(ART / "V4_Meridian_RateCard.jpg", quality=70)
    (TRUTH / "v4_degradation.json").write_text(json.dumps(meta, indent=2))
    print(f"clean {clean.size}, degraded {out.size}, level {level}, glare {args.glare}, angles {meta['angles_deg']}")


if __name__ == "__main__":
    main()
