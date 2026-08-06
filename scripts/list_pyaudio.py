import pyaudio
p = pyaudio.PyAudio()
for i in range(p.get_device_count()):
    try:
        info = p.get_device_info_by_index(i)
        print(f"{i}: {info['name']} (in: {info['maxInputChannels']}, out: {info['maxOutputChannels']})")
    except Exception as e:
        print(f"{i}: Error: {e}")
p.terminate()
