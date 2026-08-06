import pyaudio
p = pyaudio.PyAudio()
for i in range(p.get_host_api_count()):
    info = p.get_host_api_info_by_index(i)
    print(f"Host API {i}: {info['name']}")
    for j in range(info['deviceCount']):
        dev = p.get_device_info_by_host_api_device_index(i, j)
        print(f"  Device {j}: {dev['name']}")
p.terminate()
