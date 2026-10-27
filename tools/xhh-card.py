#!/usr/bin/env python3
"""小黑盒配图模板：蓝渐变 + 文字 + 鲸鱼娘贴纸。

复刻 2026-10-26《我今天才知道，我从来没查过余额》那条 3460 浏览的配图样式。

用法：
  python3 tools/xhh-card.py --text "正文" --sticker daily/xxx.png -o out.png
  python3 tools/xhh-card.py --text-file a.txt --sticker happy/xxx.png -o out.png

贴纸从 /root/.dsh/meme-packs/whale-girl-emoji-v100/memes/<分类>/<文件>
分类：happy shy daily angry sad confused（现存全是 480×480 webp，PIL 直接能读）
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'
PACK = Path('/root/.dsh/meme-packs/whale-girl-emoji-v100/memes')
# 鲸鱼娘精灵表帧（Remotion 渲染产物）：jyn/r01_03.png 这种写法直接读这里
JYN = Path('/root/video-lab/public/jyn')

TOP = (17, 53, 113)
BOT = (12, 28, 53)
WHITE = (247, 255, 255)
TEXT = (238, 245, 255)
DIM = (150, 175, 210)


def font(size):
    return ImageFont.truetype(FONT, size)


def gradient(w, h):
    img = Image.new('RGB', (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        c = tuple(int(TOP[i] + (BOT[i] - TOP[i]) * t) for i in range(3))
        d.line([(0, y), (w, y)], fill=c)
    return img


def wrap(d, text, f, width):
    """按像素宽度折行，保留手动换行。标点不放行首，开引号不放行尾。"""
    BEGIN = '，。、；：？！）】」』…—·,.!?;:)]}%'
    out = []
    for para in text.split('\n'):
        if not para:
            out.append('')
            continue
        chars = list(para)
        cur = ''
        i = 0
        while i < len(chars):
            ch = chars[i]
            if d.textlength(cur + ch, font=f) <= width:
                cur += ch
                i += 1
                continue
            # 放不下：先看下一个字是不是标点，是就一起带过去
            if cur and i < len(chars) and chars[i] in BEGIN:
                cur += chars[i]
                i += 1
            if cur:
                out.append(cur)
                cur = ''
            else:
                cur = ch
                i += 1
        if cur:
            out.append(cur)
    return out


def build(text, stickers, out, kicker='运气好的那条', footer='小鱼 · 飞燕草', size=(1080, 608), labels=None):
    w, h = size
    img = gradient(w, h)
    d = ImageDraw.Draw(img, 'RGBA')

    # 柔和光斑
    for cx, cy, r, a in [(140, 40, 240, 20), (980, 560, 260, 14), (520, 100, 220, 10)]:
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(90, 150, 230, a))

    # 右侧贴纸列：一张时是一张大卡，多张时竖着排开（最多按 4 张分）
    items = [Path(s) for s in (stickers or []) if s and Path(s).exists()]
    card_w = 300
    right = w - card_w - 60
    if len(items) <= 1:
        # 单张：右侧一张大卡，正文让出右边
        boxes = [(right, (h - card_w) // 2, card_w, card_w)]
        text_bottom = h - 100
    else:
        # 多张：底部横排一行方卡，下面挂一行字；正文让出上半部分
        n = min(len(items), 4)
        side = 210 if n <= 3 else 186
        gap = 26
        total_w = n * side + (n - 1) * gap
        x0 = (w - total_w) // 2
        label_h = 34
        y0 = h - side - label_h - 26
        boxes = [(x0 + i * (side + gap), y0, side, side) for i in range(n)]
        items = items[:n]
        right = w - 64
        text_bottom = y0 - 30
    tag_font = font(22)
    for idx, ((bx, by, bw, bh), path) in enumerate(zip(boxes, items or [None] * len(boxes))):
        d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=26, fill=WHITE)
        if path is None:
            continue
        st = Image.open(path).convert('RGBA')
        st.thumbnail((bw - 16, bh - 16))
        img.paste(st, (bx + (bw - st.width) // 2, by + (bh - st.height) // 2), st)
        if len(boxes) > 1 and labels and idx < len(labels) and labels[idx]:
            d.text((bx + bw // 2, by + bh + 8), labels[idx], font=tag_font,
                   fill=(196, 218, 245), anchor='mt')

    # 左上小标
    d.text((64, 48), kicker, font=font(24), fill=DIM)
    d.line([(64, 88), (w - 64, 88)], fill=(46, 82, 132), width=2)

    # 正文：字号自适应
    pad_l, pad_r = 64, right - 40
    avail = pad_r - pad_l
    body = None
    for fs in (44, 42, 40, 38, 36, 34, 32, 30, 28):
        f = font(fs)
        lines = wrap(d, text, f, avail)
        if len(lines) * int(fs * 1.55) <= text_bottom - 128:
            body, lines_, fs_ = f, lines, fs
            break
    if body is None:
        fs_ = 26
        body = font(fs_)
        lines_ = wrap(d, text, body, avail)[:14]

    # 正文在可用区域里垂直居中，多贴纸时下半部分不会空得太明显
    block_h = len(lines_) * int(fs_ * 1.55)
    y = 128 + max(0, (text_bottom - 128 - block_h) // 2)
    for ln in lines_:
        d.text((pad_l, y), ln, font=body, fill=TEXT)
        y += int(fs_ * 1.55)

    # 多贴纸时底部被贴纸和标签占满，署名挪到右上，别跟标签挤在一起
    if len(boxes) > 1:
        d.text((w - 64, 50), footer, font=font(20), fill=(96, 124, 168), anchor='rt')
    else:
        d.text((64, h - 46), footer, font=font(20), fill=(96, 124, 168))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--text')
    ap.add_argument('--text-file')
    ap.add_argument('--sticker', action='append', required=True,
                    help='贴纸相对路径，如 happy/xxx.png；可重复传，最多 4 张竖排')
    ap.add_argument('--label', action='append',
                    help='贴纸下面的字，写成 文件名=文字，可重复')
    ap.add_argument('--kicker', default='运气好的那条')
    ap.add_argument('--footer', default='小鱼 · 飞燕草')
    ap.add_argument('-o', '--out', default='output/xhh-card.png')
    a = ap.parse_args()
    text = a.text or Path(a.text_file).read_text(encoding='utf-8')
    label_map = {}
    for pair in a.label or []:
        if '=' in pair:
            key, val = pair.split('=', 1)
            label_map[key.strip()] = val.strip()
    stickers = []
    for raw in a.sticker:
        if Path(raw).exists():
            stickers.append(raw)
        elif raw.startswith('jyn/'):
            stickers.append(str(JYN / Path(raw).name))
        else:
            stickers.append(str(PACK / raw))
    labels = [label_map.get(Path(s).name, '') for s in stickers]
    build(text, stickers, a.out, a.kicker, a.footer, labels=labels)


if __name__ == '__main__':
    main()
