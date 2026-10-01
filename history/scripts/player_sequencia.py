#!/usr/bin/env python3
"""
Play a folder of sequence charts as a video.

Built for the output of `undercutting_valida.gerar_sequencia_minuto()`, but it
works on any folder of numbered PNGs.

Three modes, because this box has no display and no ffmpeg:

  gif   (default) animated GIF via PIL -- works headless, opens in VS Code or a
        browser, no extra dependencies. This is the one that works here.
  play  interactive Tkinter window with play/pause/step/speed. Needs a display
        ($DISPLAY or Wayland); it will tell you if there is none.
  mp4   delegates to ffmpeg if it is installed (it is not, on this machine).

Examples
--------
  python player_sequencia.py sequencia_20250930_43237000_1s
  python player_sequencia.py <pasta> --fps 4 --largura 1400
  python player_sequencia.py <pasta> --modo play
  python player_sequencia.py <pasta> --modo gif --de 0 --ate 15
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def achar_frames(pasta: Path, de: int | None, ate: int | None) -> list[Path]:
    """PNGs in the folder, in filename order (the writer zero-pads the index)."""
    frames = sorted(p for p in pasta.glob("*.png"))
    if not frames:
        raise SystemExit(f"no PNG found in {pasta}")
    if de is not None or ate is not None:
        ini = de or 0
        fim = len(frames) if ate is None else ate + 1
        frames = frames[ini:fim]
        if not frames:
            raise SystemExit("the --de/--ate range selected no frames")
    return frames


def _abrir(caminho: Path, largura: int):
    from PIL import Image

    im = Image.open(caminho)
    im.load()
    if im.mode != "RGB":
        im = im.convert("RGB")
    if largura and im.width > largura:
        alt = round(im.height * largura / im.width)
        im = im.resize((largura, alt), Image.LANCZOS)
    return im


# ---------------------------------------------------------------- GIF --------
def exportar_gif(frames: list[Path], saida: Path, fps: float, largura: int):
    from PIL import Image

    atraso = max(20, int(round(1000.0 / fps)))          # ms; browsers floor ~20ms
    print(f"Building GIF: {len(frames)} frames, {fps:g} fps ({atraso} ms/frame), "
          f"width {largura}px")

    imagens = []
    for i, f in enumerate(frames, 1):
        im = _abrir(f, largura)
        # adaptive palette per frame keeps the thin coloured lines legible
        imagens.append(im.convert("P", palette=Image.ADAPTIVE, colors=256))
        if i % 10 == 0 or i == len(frames):
            print(f"  {i}/{len(frames)} frames", flush=True)

    imagens[0].save(saida, save_all=True, append_images=imagens[1:],
                    duration=atraso, loop=0, optimize=True, disposal=2)
    mb = saida.stat().st_size / 1e6
    print(f"\n[OK] {saida}  ({mb:.1f} MB, {len(frames)} frames, "
          f"{len(frames)/fps:.1f}s per loop)")
    print("     Open it in VS Code or a browser -- it loops on its own.")
    if mb > 50:
        print("     Large file: lower --largura or --fps, or narrow with --de/--ate.")
    return saida


# ---------------------------------------------------------------- MP4 --------
def exportar_mp4(frames: list[Path], saida: Path, fps: float, largura: int):
    ff = shutil.which("ffmpeg")
    if not ff:
        raise SystemExit(
            "ffmpeg not installed on this machine -- use --modo gif instead "
            "(or: sudo apt install ffmpeg)")
    lista = saida.with_suffix(".txt")
    lista.write_text("".join(f"file '{f.resolve()}'\nduration {1/fps:.4f}\n"
                             for f in frames) + f"file '{frames[-1].resolve()}'\n")
    cmd = [ff, "-y", "-f", "concat", "-safe", "0", "-i", str(lista),
           "-vf", f"scale={largura}:-2:flags=lanczos", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", "-crf", "20", str(saida)]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    lista.unlink(missing_ok=True)
    print(f"\n[OK] {saida} ({saida.stat().st_size/1e6:.1f} MB)")
    return saida


# --------------------------------------------------------------- PLAYER ------
def tocar(frames: list[Path], fps: float, largura: int):
    """Interactive slideshow. Space=play/pause, arrows=step, +/-=speed, q=quit."""
    import os
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise SystemExit(
            "no display available in this session (DISPLAY is unset), so the "
            "interactive player cannot open a window.\n"
            "Use the default GIF mode instead:\n"
            f"    python {Path(__file__).name} <pasta> --modo gif")

    import tkinter as tk
    from PIL import ImageTk

    print(f"Loading {len(frames)} frames...")
    imgs = [_abrir(f, largura) for f in frames]

    raiz = tk.Tk()
    raiz.title(f"Sequence player -- {frames[0].parent.name}")
    raiz.configure(bg="#111111")
    lbl = tk.Label(raiz, bg="#111111")
    lbl.pack()
    barra = tk.Label(raiz, bg="#111111", fg="white",
                     font=("DejaVu Sans Mono", 11), anchor="w")
    barra.pack(fill="x")

    estado = {"i": 0, "tocando": True, "atraso": max(20, int(1000 / fps)), "ref": None}

    def desenhar():
        i = estado["i"]
        foto = ImageTk.PhotoImage(imgs[i])
        estado["ref"] = foto                       # keep a reference alive
        lbl.configure(image=foto)
        barra.configure(
            text=f" {i+1:>3}/{len(imgs)}  {frames[i].name}   "
                 f"[{'PLAY' if estado['tocando'] else 'PAUSED'}] "
                 f"{1000/estado['atraso']:.1f} fps   "
                 f"space=play/pause  <-/->=step  +/-=speed  q=quit ")

    def passo(n):
        estado["i"] = (estado["i"] + n) % len(imgs)
        desenhar()

    def laco():
        if estado["tocando"]:
            passo(1)
        raiz.after(estado["atraso"], laco)

    def tecla(ev):
        k = ev.keysym.lower()
        if k in ("q", "escape"):
            raiz.destroy()
        elif k == "space":
            estado["tocando"] = not estado["tocando"]
            desenhar()
        elif k in ("right", "period"):
            estado["tocando"] = False
            passo(1)
        elif k in ("left", "comma"):
            estado["tocando"] = False
            passo(-1)
        elif k in ("plus", "equal", "kp_add"):
            estado["atraso"] = max(20, int(estado["atraso"] / 1.5))
            desenhar()
        elif k in ("minus", "kp_subtract"):
            estado["atraso"] = min(3000, int(estado["atraso"] * 1.5))
            desenhar()
        elif k == "home":
            estado["i"] = 0
            desenhar()

    raiz.bind("<Key>", tecla)
    desenhar()
    laco()
    raiz.mainloop()


def main():
    ap = argparse.ArgumentParser(
        description="Play a folder of sequence charts as a video.")
    ap.add_argument("pasta", type=Path, help="folder holding the numbered PNGs")
    ap.add_argument("--modo", choices=("gif", "play", "mp4"), default="gif")
    ap.add_argument("--fps", type=float, default=3.0, help="frames per second (default 3)")
    ap.add_argument("--largura", type=int, default=1400,
                    help="resize width in px (default 1400; 0 keeps original)")
    ap.add_argument("--de", type=int, default=None, help="first frame index (0-based)")
    ap.add_argument("--ate", type=int, default=None, help="last frame index, inclusive")
    ap.add_argument("--saida", type=Path, default=None, help="output file path")
    a = ap.parse_args()

    if not a.pasta.is_dir():
        raise SystemExit(f"not a folder: {a.pasta}")
    frames = achar_frames(a.pasta, a.de, a.ate)
    print(f"{len(frames)} frames: {frames[0].name} .. {frames[-1].name}")

    if a.modo == "play":
        tocar(frames, a.fps, a.largura)
    elif a.modo == "mp4":
        exportar_mp4(frames, a.saida or a.pasta.with_suffix(".mp4"), a.fps, a.largura)
    else:
        exportar_gif(frames, a.saida or a.pasta.with_suffix(".gif"), a.fps, a.largura)


if __name__ == "__main__":
    main()
