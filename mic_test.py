import pyaudio
import sounddevice as sd

print("=== PyAudio device 1 info ===")
pa = pyaudio.PyAudio()
info = pa.get_device_info_by_index(1)
print(f"Default sample rate: {info['defaultSampleRate']}")
print(f"Max input channels: {info['maxInputChannels']}")
pa.terminate()

print("\n=== Sounddevice device 1 info ===")
print(sd.query_devices(1))