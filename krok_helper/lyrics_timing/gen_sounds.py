"""
生成 press.wav / release.wav / release_iidx.wav 三个音效。

Press        : 60ms 短电子 Click，"tok/click" 质感
Release      : 35ms G6→E6 Sine Drop，"ti/tsi" 质感，比 press 低约 6dB
Release IIDX : 16ms 街机数字 Click，"chk" 质感，48kHz/24bit
"""

import numpy as np
from scipy.io import wavfile
from scipy import signal

SR = 44100  # sample rate

# ── 工具函数 ──────────────────────────────────────────────────────────────────

def ms(n):
    return int(n * SR / 1000)

def highpass(audio, cutoff_hz, order=4):
    b, a = signal.butter(order, cutoff_hz / (SR / 2), btype="high")
    return signal.filtfilt(b, a, audio)

def peaking_eq(audio, freq_hz, gain_db, q=1.0):
    """单段 peaking EQ（双二阶 IIR）"""
    A = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * freq_hz / SR
    alpha = np.sin(w0) / (2 * q)
    b = [1 + alpha * A, -2 * np.cos(w0), 1 - alpha * A]
    a = [1 + alpha / A, -2 * np.cos(w0), 1 - alpha / A]
    return signal.lfilter(b, a, audio)

def freq_to_phase(freq_array):
    """从瞬时频率数组积分得到相位"""
    return 2 * np.pi * np.cumsum(freq_array) / SR

def save(path, audio, peak_db=0.0):
    """归一化后按 peak_db 缩放，存为 16-bit PCM"""
    mx = np.max(np.abs(audio))
    if mx > 0:
        audio = audio / mx * (10 ** (peak_db / 20))
    audio = np.clip(audio, -1.0, 1.0)
    wavfile.write(path, SR, (audio * 32767).astype(np.int16))
    print(f"Saved: {path}  ({len(audio)/SR*1000:.0f} ms, peak {peak_db} dB)")


# ── Press 音（60ms 短电子 Click）────────────────────────────────────────────────

def gen_press():
    n = ms(60)
    t = np.arange(n) / SR

    # 主体：频率从 900Hz 快速滑落到 120Hz，给出 "tok" 的重心
    freq = np.exp(np.linspace(np.log(900), np.log(120), n))
    phase = freq_to_phase(freq)
    body = np.sin(phase) * 0.6 + signal.sawtooth(phase, width=0.5) * 0.2

    # 冲击噪声层：模拟手指触碰的宽频冲击感
    noise = np.random.randn(n)
    noise_env = np.exp(-np.linspace(0, 12, n))
    click_layer = noise * noise_env * 0.35

    audio = body + click_layer

    # ADSR：1ms attack，快速指数衰减至 55ms
    env = np.zeros(n)
    atk = ms(1)
    env[:atk] = np.linspace(0, 1, atk)
    env[atk:] = np.exp(-np.linspace(0, 7, n - atk))
    audio *= env

    # EQ：保留中高频冲击感
    audio = highpass(audio, 80)
    audio = peaking_eq(audio, 1200, 3, q=0.8)
    audio = peaking_eq(audio, 4000, 2, q=1.0)

    return audio


# ── Release 音（35ms G6→E6 Sine Drop）──────────────────────────────────────────

def gen_release():
    n = ms(40)  # 略长保证尾音完整衰减

    # 频率扫：G6(1568Hz)→E6(1319Hz)，前 25ms 完成下滑
    sweep_n = ms(25)
    freq = np.empty(n)
    freq[:sweep_n] = np.linspace(1568, 1319, sweep_n)
    freq[sweep_n:] = 1319
    phase = freq_to_phase(freq)

    # 主合成：Sine 80% + Triangle 20%
    sine_part = np.sin(phase)
    tri_part = signal.sawtooth(phase, width=0.5)  # width=0.5 → triangle
    audio = 0.8 * sine_part + 0.2 * tri_part

    # Phigros 感：极短 White Noise（10%，15ms 衰减）
    noise = np.random.randn(n)
    noise_n = ms(15)
    noise_env = np.zeros(n)
    noise_env[:noise_n] = np.exp(-np.linspace(0, 5, noise_n))
    audio += noise * noise_env * 0.10

    # ADSR：Attack 0ms，Decay 35ms 指数衰减，Sustain 0，Release 0
    decay_n = ms(35)
    env = np.zeros(n)
    env[:decay_n] = np.exp(-np.linspace(0, 5.5, decay_n))
    audio *= env

    # EQ：High Pass 600Hz，提升高频
    audio = highpass(audio, 600)
    audio = peaking_eq(audio, 3000, 2, q=1.2)
    audio = peaking_eq(audio, 6000, 3, q=1.0)

    return audio


# ── Release IIDX（16ms 街机数字 Click，"chk"）────────────────────────────────

def gen_iidx_release():
    SR48 = 48000

    def ms48(n):
        return int(n * SR48 / 1000)

    def hp48(audio, cutoff):
        b, a = signal.butter(4, cutoff / (SR48 / 2), btype="high")
        return signal.filtfilt(b, a, audio)

    def eq48(audio, freq, gain_db, q=1.2):
        A = 10 ** (gain_db / 40)
        w0 = 2 * np.pi * freq / SR48
        alpha = np.sin(w0) / (2 * q)
        b = [1 + alpha * A, -2 * np.cos(w0), 1 - alpha * A]
        a = [1 + alpha / A, -2 * np.cos(w0), 1 - alpha / A]
        return signal.lfilter(b, a, audio)

    n = ms48(16)
    t = np.arange(n) / SR48

    # ── Layer 1：Bandpass White Noise（IIDX 版 50%）
    noise = np.random.randn(n)
    nyq = SR48 / 2
    # Center 4.5kHz，Resonance≈35% → Q≈2.0，边界取 2.5kHz / 7.5kHz
    b_bp, a_bp = signal.butter(2, [2500 / nyq, 7500 / nyq], btype="band")
    noise_bp = signal.filtfilt(b_bp, a_bp, noise)
    env1 = np.exp(-np.linspace(0, 5.5, n))  # decay 14ms → 衰减系数对齐
    env1[ms48(14):] = 0
    layer1 = noise_bp * env1 * 0.50

    # ── Layer 2：Square 3500Hz（IIDX 版 35%）
    square = signal.square(2 * np.pi * 3500 * t)
    env2 = np.zeros(n)
    d2 = ms48(5)
    env2[:d2] = np.exp(-np.linspace(0, 5.5, d2))
    layer2 = square * env2 * 0.35

    # ── Layer 3：FM Sine 6000Hz（IIDX 版 15%）
    # modulator: ratio=2.7 → 16200Hz，amount=30%（调制深度）
    mod = np.sin(2 * np.pi * 6000 * 2.7 * t) * 0.30
    carrier = np.sin(2 * np.pi * 6000 * t + mod * 2 * np.pi)
    env3 = np.zeros(n)
    d3 = ms48(10)
    env3[:d3] = np.exp(-np.linspace(0, 5.5, d3))
    layer3 = carrier * env3 * 0.15

    audio = layer1 + layer2 + layer3

    # ── EQ
    audio = hp48(audio, 1200)            # High Pass 1200Hz
    audio = eq48(audio, 500,  -12, q=0.8)  # Cut 500Hz -12dB
    audio = eq48(audio, 3500,   3, q=1.2)  # Boost 3.5kHz +3dB
    audio = eq48(audio, 6500,   5, q=1.2)  # Boost 6.5kHz +5dB

    # ── Bitcrusher（IIDX 版：5% mix，10-bit）
    quantized = np.round(audio * 512) / 512  # 2^(10-1) = 512
    audio = audio * 0.95 + quantized * 0.05

    # ── 尾部 3ms 静音
    audio = np.concatenate([audio, np.zeros(ms48(3))])

    return audio, SR48


def save_iidx(path, audio, sr, peak_db=0.0):
    """24-bit PCM 存储（scipy 用 int32 模拟，移位到高24位）"""
    mx = np.max(np.abs(audio))
    if mx > 0:
        audio = audio / mx * (10 ** (peak_db / 20))
    audio = np.clip(audio, -1.0, 1.0)
    # 24-bit：用 int32 存，值范围 -2^23 ~ 2^23-1
    data_int32 = (audio * (2**23 - 1)).astype(np.int32)
    wavfile.write(path, sr, data_int32)
    print(f"Saved: {path}  ({len(audio)/sr*1000:.0f} ms, 48kHz/24bit, peak {peak_db} dB)")


# ── Arcade Press（街机风 街机微动开关，"CHK"，12ms）────────────────────────

def gen_arcade_press():
    n = ms(15)
    t = np.arange(n) / SR

    # Layer 1：Bandpass White Noise（70%），Center 4.5kHz，Q≈2.5
    noise = np.random.randn(n)
    nyq = SR / 2
    b_bp, a_bp = signal.butter(2, [3000/nyq, 7000/nyq], btype="band")
    noise_bp = signal.filtfilt(b_bp, a_bp, noise)
    env1 = np.exp(-np.linspace(0, 6.5, n))   # Decay 10ms
    layer1 = noise_bp * env1 * 0.70

    # Layer 2：Square 3500Hz（20%），Decay 3ms
    sq = signal.square(2 * np.pi * 3500 * t)
    d2 = ms(3)
    env2 = np.zeros(n); env2[:d2] = np.exp(-np.linspace(0, 6, d2))
    layer2 = sq * env2 * 0.20

    # Layer 3：FM Sine 6500Hz（10%），Ratio 2.5，Amount 40%，Decay 6ms
    mod = np.sin(2 * np.pi * 6500 * 2.5 * t) * 0.40
    carrier = np.sin(2 * np.pi * 6500 * t + mod * 2 * np.pi)
    d3 = ms(6)
    env3 = np.zeros(n); env3[:d3] = np.exp(-np.linspace(0, 6, d3))
    layer3 = carrier * env3 * 0.10

    audio = layer1 + layer2 + layer3

    # EQ
    audio = highpass(audio, 1200)
    audio = peaking_eq(audio, 4500, 4, q=1.3)
    audio = peaking_eq(audio, 8000, 3, q=1.0)

    # 瞬态塑形：Attack +30%（首 1ms 峰值提升），Sustain -60%（3ms 后截断）
    shape = np.ones(n)
    atk = ms(1)
    shape[:atk] = np.linspace(1.0, 1.3, atk)
    tail = ms(3)
    shape[tail:] *= 0.4
    audio = audio * shape

    return audio


# ── Arcade Release（按钮回弹，"tk"，7ms）─────────────────────────────────────

def gen_arcade_release():
    n = ms(10)
    t = np.arange(n) / SR

    # Layer 1：Bandpass White Noise（80%），Center 5.5kHz
    noise = np.random.randn(n)
    nyq = SR / 2
    b_bp, a_bp = signal.butter(2, [4000/nyq, 8000/nyq], btype="band")
    noise_bp = signal.filtfilt(b_bp, a_bp, noise)
    d1 = ms(6)
    env1 = np.zeros(n); env1[:d1] = np.exp(-np.linspace(0, 6, d1))
    layer1 = noise_bp * env1 * 0.80

    # Layer 2：Triangle 2000Hz 带音高下滑 -4 semitones（80%→63%），Decay 8ms（10%）
    f_start = 2000.0
    f_end = f_start * (2 ** (-4 / 12))          # ≈ 1587Hz
    d2 = ms(8)
    freq_arr = np.empty(n)
    freq_arr[:d2] = np.linspace(f_start, f_end, d2)
    freq_arr[d2:] = f_end
    phase2 = freq_to_phase(freq_arr)
    tri = signal.sawtooth(phase2, width=0.5)
    env2 = np.exp(-np.linspace(0, 5.5, n))
    layer2 = tri * env2 * 0.10

    # Layer 3：Square 5000Hz（10%），Decay 2ms
    sq = signal.square(2 * np.pi * 5000 * t)
    d3 = ms(2)
    env3 = np.zeros(n); env3[:d3] = np.exp(-np.linspace(0, 5, d3))
    layer3 = sq * env3 * 0.10

    audio = layer1 + layer2 + layer3

    # EQ
    audio = highpass(audio, 1500)
    audio = peaking_eq(audio, 6000, 3, q=1.2)
    audio = peaking_eq(audio, 3000, -2, q=1.0)

    return audio


# ── Sci-Fi Press（金属风，"tsi↑"，25ms）────────────────────────────────

def gen_sci_press():
    n = ms(28)
    t = np.arange(n) / SR

    # Osc A：Sine 1800Hz → 上滑 +7 semitones（≈2545Hz），18ms 内完成
    sweep_n = ms(18)
    f_start = 1800.0
    f_end = f_start * (2 ** (7 / 12))          # ≈ 2545Hz
    freq_arr = np.empty(n)
    freq_arr[:sweep_n] = np.linspace(f_start, f_end, sweep_n)
    freq_arr[sweep_n:] = f_end
    phase = freq_to_phase(freq_arr)
    sine = np.sin(phase)
    amp_env = np.exp(-np.linspace(0, 5.5, n))   # Decay 25ms
    osc = sine * amp_env

    # Bright Noise（HP 3kHz），40%，Decay 20ms
    noise = np.random.randn(n)
    noise_bright = highpass(noise, 3000)
    d_noise = ms(20)
    noise_env = np.zeros(n); noise_env[:d_noise] = np.exp(-np.linspace(0, 5, d_noise))
    noise_part = noise_bright * noise_env * 0.40

    audio = osc * 0.60 + noise_part

    # EQ：强调 2~5kHz（Press 频谱）
    audio = peaking_eq(audio, 4000, 3, q=1.2)
    audio = peaking_eq(audio, 8000, 4, q=1.0)

    return audio


# ── Sci-Fi Release（金属风，"tsi↓"，18ms）──────────────────────────────

def gen_sci_release():
    n = ms(22)
    t = np.arange(n) / SR

    # Osc A：Sine 2500Hz → 下滑 -7 semitones（≈1769Hz），18ms 内完成
    sweep_n = ms(18)
    f_start = 2500.0
    f_end = f_start * (2 ** (-7 / 12))         # ≈ 1769Hz
    freq_arr = np.empty(n)
    freq_arr[:sweep_n] = np.linspace(f_start, f_end, sweep_n)
    freq_arr[sweep_n:] = f_end
    phase = freq_to_phase(freq_arr)
    sine = np.sin(phase)
    amp_env = np.exp(-np.linspace(0, 5.5, n))   # Decay 18ms
    osc = sine * amp_env

    # Bright Noise（HP 3kHz），35%，Decay 18ms
    noise = np.random.randn(n)
    noise_bright = highpass(noise, 3000)
    d_noise = ms(18)
    noise_env = np.zeros(n); noise_env[:d_noise] = np.exp(-np.linspace(0, 5, d_noise))
    noise_part = noise_bright * noise_env * 0.35

    audio = osc * 0.65 + noise_part

    # EQ：Release 强调 6~10kHz，与 Press 频谱互补，高速连打不互掩
    audio = peaking_eq(audio, 7000, 3, q=1.0)
    audio = peaking_eq(audio, 3500, -2, q=1.2)

    return audio


# ── 生成并保存 ─────────────────────────────────────────────────────────────────

OUT = r"src/strange_uta_game/resource/sounds"

press = gen_press()
release = gen_release()
iidx_audio, iidx_sr = gen_iidx_release()

save(f"{OUT}/press.wav",        press,   peak_db=-2.0)
save(f"{OUT}/release.wav",      release, peak_db=-4.0)   # 比 press 低 2dB
save_iidx(f"{OUT}/release_iidx.wav", iidx_audio, iidx_sr, peak_db=-6.0)  # 比 press 低 4dB

# Arcade（街机风 街机风格）
arcade_p = gen_arcade_press()
arcade_r = gen_arcade_release()
save(f"{OUT}/arcade_press.wav",   arcade_p, peak_db=-2.0)
save(f"{OUT}/arcade_release.wav", arcade_r, peak_db=-4.0)  # 比 press 低 2dB

# Sci-Fi（金属风 风格）
sci_p = gen_sci_press()
sci_r = gen_sci_release()
save(f"{OUT}/sci_press.wav",   sci_p, peak_db=-2.0)
save(f"{OUT}/sci_release.wav", sci_r, peak_db=-4.0)   # 比 press 低 2dB
