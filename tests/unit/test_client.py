import os, pytest, unittest
from bin import client


class TestStreamingClient(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def rootpath(self, pytestconfig):
        self.rootpath = pytestconfig.rootpath
        self.datapath = pytestconfig.rootpath.joinpath("tests/unit/data")
        self.audio_8k_path = os.path.join(self.datapath, "en-us.8k.wav")
        self.audio_16k_path = os.path.join(self.datapath, "en-us.16k.wav")
        self.audio_24b_path = os.path.join(self.datapath, "en-us.24b.wav")

    def testAudioChunking(self):
        audio_bytes = [i for i in range(10)]
        chunk_iterator = client._chunk_audio(audio_bytes, 3)
        self.assertEqual(list(chunk_iterator), [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]])

    def testAudioChunking0(self):
        audio_bytes = [i for i in range(10)]
        chunk_iterator = client._chunk_audio(audio_bytes, 0)
        self.assertEqual(list(chunk_iterator), [audio_bytes])

    def testAudioChunkingEmpty(self):
        audio_bytes = []
        chunk_iterator = client._chunk_audio(audio_bytes, 3)
        self.assertEqual(list(chunk_iterator), [[]])

    def testAudioChunking0EmptyAudio(self):
        audio_bytes = []
        chunk_iterator = client._chunk_audio(audio_bytes, 0)
        self.assertEqual(list(chunk_iterator), [[]])

    def testGetAudio(self):
        _audioBytes, rate, width = client._getAudio(self.audio_8k_path)
        self.assertEqual(width, 2)
        self.assertEqual(rate, 8_000)
        _audioBytes, rate, width = client._getAudio(self.audio_16k_path)
        self.assertEqual(width, 2)
        self.assertEqual(rate, 16_000)
        with self.assertRaises(Exception):
            client._getAudio(self.audio_24b_path)
