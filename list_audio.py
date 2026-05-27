import pyaudio

def list_audio_devices():
    p = pyaudio.PyAudio()
    print("\n--- Dispositivi Audio Rilevati ---")
    info = p.get_host_api_info_by_index(0)
    numdevices = info.get('deviceCount')
    
    for i in range(0, numdevices):
        dev_info = p.get_device_info_by_host_api_device_index(0, i)
        print(f"Index {i}: {dev_info.get('name')} (Input: {dev_info.get('maxInputChannels')}, Output: {dev_info.get('maxOutputChannels')})")
    
    p.terminate()

if __name__ == "__main__":
    list_audio_devices()
