"""Assemble the AI-Shtat Pay demo video from motion, Veo b-roll, screen recordings, VO and music.
Usage: python assemble.py SRC_DIR OUT.mp4   (SRC_DIR holds the files named in CLIPS)"""
import subprocess, sys, os, json
SRC, OUT = sys.argv[1], sys.argv[2]
TMP = os.path.join(os.path.dirname(OUT) or ".", "_seg"); os.makedirs(TMP, exist_ok=True)
W, H, FPS = 1920, 1080, 30
BG = "0x0A0E17"
def sh(*a): subprocess.run(a, check=True)
def norm(src, ss, dur, out, vf_extra="", speed=1.0):
    vf = f"setpts=PTS/{speed},scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={BG},fps={FPS},format=yuv420p"
    if vf_extra: vf = vf_extra + "," + vf
    sh("ffmpeg","-v","error","-y","-ss",str(ss),"-t",str(dur*speed),"-i",src,"-an","-vf",vf,"-c:v","libx264","-preset","veryfast","-crf","18",out)
def phone(src, ss, dur, out, speed=1.0):
    # vertical Phantom panel centered over blurred, darkened copy
    fc = (f"[0:v]setpts=PTS/{speed},crop=420:1020:1458:0,split[a][b];"
          f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30,eq=brightness=-0.35[bg];"
          f"[b]scale=-2:1000[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,fps={FPS},format=yuv420p")
    sh("ffmpeg","-v","error","-y","-ss",str(ss),"-t",str(dur*speed),"-i",src,"-an","-filter_complex",fc,"-c:v","libx264","-preset","veryfast","-crf","18",out)
P = lambda n: os.path.join(SRC, n)
CFG = json.load(open(os.path.join(os.path.dirname(__file__), "timeline.json")))
segs = []
for i, c in enumerate(CFG["segments"]):
    out = os.path.join(TMP, f"s{i:02d}.mp4")
    if c.get("kind") == "phone": phone(P(c["src"]), c["ss"], c["dur"], out, c.get("speed", 1.0))
    else: norm(P(c["src"]), c["ss"], c["dur"], out, c.get("vf", ""), c.get("speed", 1.0))
    segs.append((out, c["dur"], c.get("vo")))
lst = os.path.join(TMP, "list.txt")
open(lst, "w").write("".join(f"file '{os.path.abspath(s)}'\n" for s, _, _ in segs))
video = os.path.join(TMP, "video.mp4")
sh("ffmpeg","-v","error","-y","-f","concat","-safe","0","-i",lst,"-c","copy",video)
total = sum(d for _, d, _ in segs)
# audio: VO lines at segment start + offset, music ducked under VO
inputs, filt, t = ["-stream_loop", "-1", "-i", P(CFG["music"])], [], 0.0
vo_labels = []
for k, (_, d, vo) in enumerate(segs):
    if vo:
        inputs += ["-i", P(vo["file"])]; n = len(inputs)//2 - 1  # video=0, music=1 (4 args), VOs from 2
        ms = int((t + vo.get("at", 0.3)) * 1000)
        filt.append(f"[{n}:a]aresample=48000,adelay={ms}|{ms},volume=1.6[v{n}]"); vo_labels.append(f"[v{n}]")
    t += d
filt.append(f"{''.join(vo_labels)}amix=inputs={len(vo_labels)}:normalize=0,apad[vo]")
filt.append(f"[1:a]aresample=48000,atrim=0:{total},afade=t=out:st={total-2.5}:d=2.5,volume=0.35[mus]")
filt.append(f"[vo]asplit[vo1][vo2];[mus][vo2]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[duck];[duck][vo1]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.95,atrim=0:{total}[a]")
sh("ffmpeg","-v","error","-y","-i",video,*inputs,"-filter_complex",";".join(filt),"-map","0:v","-map","[a]","-c:v","copy","-c:a","aac","-b:a","192k","-t",str(total),OUT)
print("total", total)
