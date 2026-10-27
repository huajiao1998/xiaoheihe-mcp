#!/usr/bin/env python3
"""把一张贴纸做成 1080×1080 的单图，供发帖时单独跟在正文后面。

手机上正文卡片的贴纸永远偏小，单独发一张就不一样了：一张图一个表情，
屏幕铺满，看得清脸。

用法：
  # 只放大贴纸，不加字
  python3 tools/xhh-sticker.py happy/deepsleep.webp -o out.png

  # 贴纸 + 一句台词（贴在下方）
  python3 tools/xhh-sticker.py daily/xxx.webp --text "上班摸鱼" -o out.png

  # 深色底（默认）或浅色底
  python3 tools/xhh-sticker.py angry/xxx.webp --light -o out.png
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'
PACK = Path('/root/.dsh/meme-packs/whale-girl-emoji-v100/memes')

TOP = (17, 53, 113)
BOT = (12, 28, 53)
LIGHT_TOP = (247, 249, 253)
LIGHT_BOT = (219, 231, 246)
WHITE = (247, 255, 255)
DARK = (28, 44, 72)


def font(size):
    return ImageFont.truetype(FONT, size)


def gradient(w, h, light=False):
    a, b = (LIGHT_TOP, LIGHT_BOT) if light else (TOP, BOT)
    img = Image.new('RGB', (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)))
    return img


def resolve(raw):
    p = Path(raw)
    return p if p.is_file() else PACK / raw


def build(sticker, out, text='', dark=False, size=1080):
    st = Image.open(resolve(sticker)).convert('RGBA')
    # 贴纸四周常有大片透明留白，裁掉再放大，脸才够大
    box = st.split()[3].point(lambda v: 255 if v > 8 else 0).getbbox()
    if box:
        pad = 8
        st = st.crop((max(0, box[0] - pad), max(0, box[1] - pad),
                      min(st.width, box[2] + pad), min(st.height, box[3] + pad)))

    # 默认纯白底：不加卡框、不加渐变，贴纸直接铺在纸上，看着像表情包本身
    if dark:
        img = gradient(size, size)
    else:
        img = Image.new('RGB', (size, size), (255, 255, 255))
    d = ImageDraw.Draw(img, 'RGBA')

    area = size - 120
    st.thumbnail((area, area), Image.LANCZOS)
    top = (size - st.height) // 2 - (40 if text else 0)
    img.paste(st, ((size - st.width) // 2, max(24, top)), st)

    if text:
        f = font(54)
        while d.textlength(text, font=f) > size - 160 and f.size > 30:
            f = font(f.size - 4)
        d.text((size // 2, size - 90), text, font=f,
               fill=(226, 238, 252) if dark else (38, 52, 78), anchor='mm')

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('sticker', help='贴纸相对路径，如 happy/xxx.webp；也接受绝对路径')
    ap.add_argument('--text', default='', help='图下方的字，不写就只放大图')
    ap.add_argument('--dark', action='store_true', help='深蓝底（默认白底）')
    ap.add_argument('-o', '--out', default='output/xhh-sticker.png')
    a = ap.parse_args()
    build(a.sticker, a.out, a.text, a.dark)


if __name__ == '__main__':
    main()
