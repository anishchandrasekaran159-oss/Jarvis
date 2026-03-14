import sounddevice as sd
import soundfile as sf
import numpy as np

SAMPLE_RATE = 16000
CHANNELS = 1

print("Recording for 5 seconds... speak now")
audio = sd.rec(int(5 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='float32')
sd.wait()
sf.write("mic_test.wav", audio, SAMPLE_RATE)
print("Saved to mic_test.wav")