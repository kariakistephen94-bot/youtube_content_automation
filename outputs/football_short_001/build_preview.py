"""Build a 45-second vertical editorial preview with original graphics and local TTS."""
from pathlib import Path
import json
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parent
WORK = ROOT / 'render_assets'
WORK.mkdir(exist_ok=True)
FONT = '/System/Library/Fonts/Supplemental/Arial Bold.ttf'
SCENES = [
    (5, 'NINE GOALS?', 'THE BIGGER STORY', 'Yamal scored nine goals in September. But that is only half the story.'),
    (5, '9 GOALS\n7 ASSISTS', 'CLUB + COUNTRY | SEPTEMBER 2026', 'Barcelona says he also added seven assists. Sixteen goal contributions in one month.'),
    (6, 'THE DETAIL\nTHAT MATTERS', 'CONSISTENCY', 'Here is the detail that matters: he delivered at least two goal contributions in every Barcelona appearance.'),
    (7, '5 GAMES\n12 CONTRIBUTIONS', 'BARCELONA | 6 GOALS + 6 ASSISTS', 'Five games. Six goals, six assists. So the story goes beyond one spectacular performance.'),
    (6, 'THEN\nSPAIN', '3 GOALS + 1 ASSIST', 'Then he carried it into the international break, scoring against England and twice against Croatia.'),
    (6, 'HIS BEST\nMONTH YET', 'MONTHLY GOALS + ASSISTS', 'According to Barcelona, this was his most productive month yet for goals and assists.'),
    (5, 'FINISHER\n+ CREATOR', 'OUR TAKE', 'My takeaway? Judge him as a finisher and a creator. The assists change the picture.'),
    (5, 'GOALS\nOR CREATIVITY?', 'WHAT MATTERS MORE TO YOU?', 'What matters more when you judge a winger: scoring goals, or creating them?'),
]

def run(args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

def duration(path):
    return float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)]))

def stamp(t, ass=False):
    if ass:
        return f'{int(t)//3600}:{int(t)//60%60:02}:{int(t)%60:02}.{round(t%1*100):02}'
    ms = round(t*1000)
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'

subtitles=[]
offset=0
for i,(seconds,headline,kicker,narration) in enumerate(SCENES):
    prefix=WORK / f'{i:02}'
    txt=prefix.with_suffix('.txt')
    txt.write_text(narration)
    raw=prefix.with_suffix('.aiff')
    run(['say','-v','Daniel','-r','172','-f',str(txt),'-o',str(raw)])
    speech_time=seconds-0.35
    tempo=duration(raw)/speech_time
    wav=prefix.with_suffix('.wav')
    run(['ffmpeg','-y','-i',str(raw),'-af',f'atempo={tempo:.6f},apad,atrim=duration={seconds}',str(wav)])
    title=WORK / f'{i:02}_headline.txt'
    label=WORK / f'{i:02}_label.txt'
    title.write_text(headline)
    label.write_text(kicker)
    chunks=textwrap.wrap(narration, width=46)
    grouped=['\n'.join(chunks[j:j+2]) for j in range(0,len(chunks),2)]
    step=speech_time/len(grouped)
    ass=WORK / f'{i:02}.ass'
    header='[Script Info]\nPlayResX: 1080\nPlayResY: 1920\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Arial,48,&H00FFFFFF,&H00FFFFFF,&H00100A08,&H80100A08,-1,0,0,0,100,100,0,0,1,3,0,2,95,95,370,1\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
    events=[]
    for j,chunk in enumerate(grouped):
        start=j*step
        end=(j+1)*step
        events.append(f'Dialogue: 0,{stamp(start,True)},{stamp(end,True)},Default,,0,0,0,,'+chunk.replace('\n',r'\N'))
        subtitles.append((offset+start,offset+end,chunk))
    ass.write_text(header+'\n'.join(events)+'\n')
    vf=','.join([
        'drawbox=x=0:y=0:w=1080:h=1920:color=0x0b1224:t=fill',
        'drawbox=x=65:y=240:w=12:h=990:color=0x56e2c0:t=fill',
        'drawbox=x=83:y=285:w=930:h=3:color=0x35465d:t=fill',
        f"drawtext=fontfile='{FONT}':text='LAMINE YAMAL':fontsize=40:fontcolor=white:x=100:y=210",
        f"drawtext=fontfile='{FONT}':textfile='{title}':fontsize=85:fontcolor=0x56e2c0:x=(w-text_w)/2:y=530:line_spacing=25",
        f"drawtext=fontfile='{FONT}':textfile='{label}':fontsize=32:fontcolor=white:x=(w-text_w)/2:y=945",
        f"drawtext=fontfile='{FONT}':text='SOURCE  FC BARCELONA / 30 SEP 2026':fontsize=24:fontcolor=0xaab9cc:x=100:y=1660",
        f"drawtext=fontfile='{FONT}':text='EDITORIAL PREVIEW / ORIGINAL GRAPHICS':fontsize=20:fontcolor=0x778ba8:x=100:y=1710",
        f"drawbox=x=100:y=1770:w={int(880*(offset+seconds)/45)}:h=6:color=0x56e2c0:t=fill",
        f"ass='{ass}'",
    ])
    out=WORK / f'{i:02}.mp4'
    run(['ffmpeg','-y','-f','lavfi','-i',f'color=c=0x0b1224:s=1080x1920:r=30:d={seconds}','-i',str(wav),'-vf',vf,'-t',str(seconds),'-c:v','libx264','-preset','veryfast','-crf','22','-pix_fmt','yuv420p','-c:a','aac','-b:a','160k',str(out)])
    offset+=seconds
    print(f'Rendered scene {i+1}/8',flush=True)

concat=WORK / 'concat.txt'
concat.write_text('\n'.join(f"file '{WORK / f'{i:02}.mp4'}'" for i in range(len(SCENES))))
final=ROOT / 'first_clip_45s_preview.mp4'
run(['ffmpeg','-y','-f','concat','-safe','0','-i',str(concat),'-vf','setpts=PTS-STARTPTS','-af','asetpts=PTS-STARTPTS','-t','45','-c:v','libx264','-preset','veryfast','-crf','22','-c:a','aac','-b:a','160k','-movflags','+faststart',str(final)])
(ROOT/'voiceover.txt').write_text('\n\n'.join(s[3] for s in SCENES)+'\n')
(ROOT/'captions.srt').write_text('\n\n'.join(f'{i+1}\n{stamp(start)} --> {stamp(end)}\n{text}' for i,(start,end,text) in enumerate(subtitles))+'\n')
run(['ffmpeg','-y','-ss','6','-i',str(final),'-frames:v','1',str(ROOT/'preview_frame.png')])
metadata={'duration_seconds':duration(final),'width':1080,'height':1920,'narration':'macOS Daniel synthetic voice','visuals':'Original graphic cards; sourced match B-roll is not included','source':'https://www.fcbarcelona.com/en/football/first-team/news/4584279/spectacular-september-for-lamine-yamal/featured'}
(ROOT/'preview_metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
print(json.dumps(metadata),flush=True)
