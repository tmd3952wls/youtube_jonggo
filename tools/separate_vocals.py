"""원본 소리에서 대사(목소리)만 분리 → source_vocals.wav (Demucs htdemucs).
사용: .venv-whisperx/Scripts/python.exe -I tools/separate_vocals.py <in.wav(44.1k 스테레오)> <out_vocals.wav>
(render.py 가 source_vocals.wav 가 없으면 자동으로 실행함)"""
import sys

import numpy as np
import soundfile as sf
import torch
from demucs.apply import apply_model
from demucs.pretrained import get_model

src, dst = sys.argv[1], sys.argv[2]
model = get_model("htdemucs")
model.eval()
wav, sr = sf.read(src, dtype="float32", always_2d=True)
assert sr == model.samplerate, (sr, model.samplerate)
x = torch.from_numpy(wav.T.copy())
ref = x.mean(0)
x = (x - ref.mean()) / (ref.std() + 1e-8)
with torch.no_grad():
    out = apply_model(model, x[None], device="cpu", split=True, overlap=0.25, progress=True)[0]
out = out * (ref.std() + 1e-8) + ref.mean()
vocals = out[model.sources.index("vocals")].numpy().T
sf.write(dst, vocals.astype(np.float32), sr, subtype="PCM_16")
print("ok", dst, vocals.shape)
