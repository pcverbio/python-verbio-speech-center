import unittest
import logging
import random
import string
import tempfile
import numpy as np
import argparse

from asr4.recognizer import Duration
from asr4.recognizer import RecognizerService
from asr4.recognizer import RecognitionServiceConfiguration
from asr4.recognizer import RecognizeRequest
from asr4.recognizer import StreamingRecognizeRequest
from asr4.recognizer import RecognitionConfig
from asr4.recognizer import RecognitionParameters
from asr4.recognizer import RecognitionResource
from asr4.recognizer import RecognizeResponse
from asr4.recognizer import StreamingRecognizeResponse
from asr4.recognizer import StreamingRecognitionResult
from asr4.recognizer import Session, OnnxRuntime
from asr4.types.language import Language
from asr4.recognizer_v1.service import TranscriptionResult
from asr4.recognizer_v1.formatter import FormatterFactory
import os

from typing import Any, Dict, List, Optional, Union

DEFAULT_ENGLISH_MESSAGE: str = "hello i am up and running received a message from you"
DEFAULT_SPANISH_MESSAGE: str = (
    "hola estoy  levantado y en marcha  y he recibido un mensaje tuyo"
)
DEFAULT_CORRECT_SPANISH_MESSAGE: str = (
    "hola estoy levantado y en marcha y he recibido un mensaje tuyo"
)
FORMATTED_SPANISH_MESSAGE: str = (
    "Hola. Estoy levantado y en marcha y he recibido un mensaje tuyo."
)
DEFAULT_PORTUGUESE_MESSAGE: str = "ola  estou de pe recebi uma mensagem sua"
DEFAULT_CORRECT_PORTUGUESE_MESSAGE: str = "ola estou de pe recebi uma mensagem sua"


class MockFormatter:
    def __init__(self, correct_sentence: str):
        self._correct_sentence = correct_sentence.split(" ")

    def classify(self, sentence: List[str]) -> List[str]:
        return self._correct_sentence


class MockArguments(argparse.Namespace):
    def __init__(self):
        super().__init__()
        self.vocabularyLabels = ["|", "<s>", "</s>", "<pad>"]
        self.vocabulary = self.createVocabulary()
        self.formatter = "path_to_formatter/formatter.fm"
        self.language = "es"
        self.model = "path_to_models/model.onnx"
        self.gpu = False
        self.workers = 4
        self.lexicon = None
        self.lm_model = None
        self.lm_algorithm = "viterbi"
        self.lm_weight = None
        self.word_score = None
        self.sil_score = None
        self.overlap = None
        self.subwords = None

    def createVocabulary(self) -> str:
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            vocabularyPath = f.name
            for label in self.vocabularyLabels:
                f.write(f"{label}\n")
        return vocabularyPath

    def getVocabularyLabels(self):
        return self.vocabularyLabels


class MockRecognitionServiceConfiguration(RecognitionServiceConfiguration):
    def __init__(self, arguments: MockArguments = MockArguments()):
        super().__init__(arguments)

    def createOnnxSession(self):
        return MockOnnxSession(
            self.model,
            language=self.language,
        )


class MockOnnxSession(Session):
    def __init__(self, _path_or_bytes: Union[str, bytes], **kwargs) -> None:
        super().__init__(_path_or_bytes, **kwargs)
        self.logger = logging.getLogger("TEST")
        self._message = {
            Language.EN_US: DEFAULT_ENGLISH_MESSAGE,
            Language.ES: DEFAULT_SPANISH_MESSAGE,
            Language.PT_BR: DEFAULT_PORTUGUESE_MESSAGE,
        }.get(kwargs.get("language"), DEFAULT_ENGLISH_MESSAGE)

    def run(
        self,
        _output_names: Optional[List[str]],
        input_feed: Dict[str, Any],
        **kwargs,
    ) -> np.ndarray:
        defaultMessage = list(self._message.replace(" ", "|"))
        return [self._generateDefaultMessageArray(defaultMessage)]

    def _generateDefaultMessageArray(self, defaultMessage: List[str]) -> np.ndarray:
        defaultMessageArray = np.full(
            (1, len(defaultMessage), len(OnnxRuntime.DEFAULT_VOCABULARY)),
            -10.0,
            np.float32,
        )
        for i, letter in enumerate(defaultMessage):
            defaultMessageArray[
                0, i, OnnxRuntime.DEFAULT_VOCABULARY.index(letter)
            ] = 10.0
        return self._insertBlankBetweenRepeatedLetters(
            defaultMessage, defaultMessageArray
        )

    def _insertBlankBetweenRepeatedLetters(
        self, defaultMessage: List[str], defaultMessageArray: np.ndarray
    ) -> np.ndarray:
        lastLetter, offset = "", 0
        blank_row = self._getBlankArray()
        for i, letter in enumerate(defaultMessage):
            if lastLetter == letter:
                defaultMessageArray = np.insert(
                    defaultMessageArray, i + offset, blank_row, axis=1
                )
                offset += 1
            lastLetter = letter
        return defaultMessageArray

    def _getBlankArray(self) -> np.ndarray:
        blank_row = np.zeros(len(OnnxRuntime.DEFAULT_VOCABULARY), dtype=np.float32)
        blank_row[OnnxRuntime.DEFAULT_VOCABULARY.index("<s>")] = 10.0
        return blank_row

    def get_inputs_names(self) -> List[str]:
        return ["input"]


class TestRecognizerServiceConfiguration(unittest.TestCase):
    def testInit(self):
        arguments = MockArguments()
        configuration = RecognitionServiceConfiguration(arguments)
        self.assertEqual(configuration.language, Language.parse(arguments.language))
        self.assertEqual(configuration.model, arguments.model)
        self.assertEqual(configuration.gpu, arguments.gpu)
        self.assertEqual(configuration.formatterModelPath, arguments.formatter)
        self.assertEqual(configuration.vocabulary, arguments.vocabulary)
        self.assertEqual(configuration.numberOfWorkers, arguments.workers)

    def testEmpyInit(self):
        configuration = RecognitionServiceConfiguration()
        self.assertIsNotNone(configuration)
        self.assertEqual(type(configuration), RecognitionServiceConfiguration)


class TestRecognizerService(unittest.TestCase):
    def testNoExistentVocabulary(self):
        with self.assertRaises(FileNotFoundError):
            configuration = MockRecognitionServiceConfiguration()
            configuration.vocabulary = "file_that_doesnt_exist"
            RecognizerService(configuration)

    def testEmptyvocabularyPath(self):
        with self.assertRaises(FileNotFoundError):
            configuration = MockRecognitionServiceConfiguration()
            configuration.vocabulary = ""
            RecognizerService(configuration)

    def testVocabulary(self):
        arguments = MockArguments()
        configuration = MockRecognitionServiceConfiguration(arguments)
        service = RecognizerService(configuration)
        self.assertEqual(
            service._runtime._decoder.labels, arguments.getVocabularyLabels()
        )

    def testInvalidAudio(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = RecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(
                    language="en-US", sample_rate_hz=16000, audio_encoding="PCM"
                ),
                resource=RecognitionResource(topic="GENERIC"),
            ),
            audio=b"",
        )
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testInvalidTopic(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = RecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(
                    language="en-US", sample_rate_hz=16000, audio_encoding="PCM"
                ),
                resource=RecognitionResource(topic=-1),
            ),
            audio=b"SOMETHING",
        )
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testInvalidAudioEncoding(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = RecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(
                    language="en-US", sample_rate_hz=16000, audio_encoding=2
                ),
                resource=RecognitionResource(topic=-1),
            ),
            audio=b"SOMETHING",
        )
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testInvalidLanguage(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = RecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(language="", sample_rate_hz=16000),
                resource=RecognitionResource(topic="GENERIC"),
            ),
            audio=b"SOMETHING",
        )
        with self.assertRaises(ValueError):
            service.eventSource(request)
        request = RecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(
                    language="INVALID", sample_rate_hz=16000
                ),
                resource=RecognitionResource(topic="GENERIC"),
            ),
            audio=b"SOMETHING",
        )
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testInvalidSampleRate(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = RecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(
                    language="en-US", sample_rate_hz=16001
                ),
                resource=RecognitionResource(topic="GENERIC"),
            ),
            audio=b"SOMETHING",
        )
        with self.assertRaises(ValueError):
            service.eventSource(request)

        request = RecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(language="en-US", sample_rate_hz=8001),
                resource=RecognitionResource(topic="GENERIC"),
            ),
            audio=b"SOMETHING",
        )
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testInvalidRecognizeRequestEmpty(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = RecognizeRequest()
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testInvalidStreamingRecognizeRequestEmpty(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = StreamingRecognizeRequest()
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testInvalidRecognizeRequestAudio(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = RecognizeRequest(audio=b"SOMETHING")
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testInvalidStreamingRecognizeRequestAudio(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = StreamingRecognizeRequest(audio=b"SOMETHING")
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testInvalidRecognizeRequestResource(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = RecognizeRequest(
            config=RecognitionConfig(resource=RecognitionResource(topic="GENERIC"))
        )
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testInvalidStreamingRecognizeRequestResource(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = StreamingRecognizeRequest(
            config=RecognitionConfig(resource=RecognitionResource(topic="GENERIC"))
        )
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testInvalidRecognizeRequestLanguage(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = RecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(language="en-US"),
            )
        )
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testInvalidStreamingRecognizeRequestLanguage(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = StreamingRecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(language="en-US"),
            )
        )
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testInvalidRecognizeRequestAudioEncoding(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = RecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(audio_encoding="PCM"),
            )
        )
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testInvalidRecognizeRequestSampleRate(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = RecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(sample_rate_hz=4000),
            )
        )
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testInvalidStreamingRecognizeRequestSampleRate(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = StreamingRecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(sample_rate_hz=4000),
            )
        )
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testInvalidRecognizeRequestParameters(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = RecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(
                    language="en-US", sample_rate_hz=16000, audio_encoding="PCM"
                ),
            )
        )
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testInvalidStreamingRecognizeRequestParameters(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = StreamingRecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(
                    language="en-US", sample_rate_hz=16000, audio_encoding="PCM"
                ),
            )
        )
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testInvalidRecognizeRequestAudioEncodingValue(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = RecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(
                    language="en-US", sample_rate_hz=16000, audio_encoding="PCM"
                ),
                resource=RecognitionResource(topic="GENERIC"),
            )
        )
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testInvalidStreamingRecognizeRequestAudioEncodingValue(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = StreamingRecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(
                    language="en-US", sample_rate_hz=16000, audio_encoding="PCM"
                ),
                resource=RecognitionResource(topic="GENERIC"),
            )
        )
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testInvalidRecognizeRequestConfig(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = RecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(
                    language="en-US", sample_rate_hz=16000, audio_encoding=1
                ),
                resource=RecognitionResource(topic="GENERIC"),
            ),
            audio=b"SOMETHING",
        )
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testInvalidStreamingRecognizeRequestConfig(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = StreamingRecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(
                    language="en-US", sample_rate_hz=16000, audio_encoding="PCM"
                ),
                resource=RecognitionResource(topic="GENERIC"),
            )
        )
        with self.assertRaises(ValueError):
            service.eventSource(request)

    def testRecognizeRequestSampleRate16000(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = RecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(
                    language="en-US", sample_rate_hz=16000, audio_encoding="PCM"
                ),
                resource=RecognitionResource(topic="GENERIC"),
            ),
            audio=b"SOMETHING",
        )
        self.assertFalse(service.eventSource(request))

    def testRecognizeRequestSampleRate8000(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = RecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(
                    language="en-US", sample_rate_hz=8000, audio_encoding="PCM"
                ),
                resource=RecognitionResource(topic="GENERIC"),
            ),
            audio=b"SOMETHING",
        )
        self.assertFalse(service.eventSource(request))

    def testInvalidRecognizeRequestHandle(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = RecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(),
            )
        )
        with self.assertRaises(ValueError):
            service.eventHandle(request)

    def testInvalidStreamingRecognizeRequestHandle(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        request = StreamingRecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(),
            )
        )
        with self.assertRaises(ValueError):
            service.eventHandle(request)

    def testRecognizeRequestHandleEnUs(self):
        arguments = MockArguments()
        arguments.language = Language.EN_US
        arguments.vocabulary = None
        service = RecognizerService(MockRecognitionServiceConfiguration(arguments))
        request = RecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(
                    language="en-US",
                    sample_rate_hz=8000,
                    audio_encoding="PCM",
                    enable_formatting=False,
                ),
                resource=RecognitionResource(topic="GENERIC"),
            ),
            audio=b"0000",
        )
        self.assertEqual(
            service.eventHandle(request).transcription,
            DEFAULT_ENGLISH_MESSAGE,
        )

    def testRecognizeRequestHandleEs(self):
        arguments = MockArguments()
        arguments.language = Language.ES
        arguments.vocabulary = None
        service = RecognizerService(MockRecognitionServiceConfiguration(arguments))
        request = RecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(
                    language="es",
                    sample_rate_hz=8000,
                    audio_encoding="PCM",
                    enable_formatting=False,
                ),
                resource=RecognitionResource(topic="GENERIC"),
            ),
            audio=b"0000",
        )
        self.assertEqual(
            service.eventHandle(request).transcription,
            DEFAULT_CORRECT_SPANISH_MESSAGE,
        )

    def testRecognizeRequestHandlePtBr(self):
        arguments = MockArguments()
        arguments.language = Language.PT_BR
        arguments.vocabulary = None
        service = RecognizerService(MockRecognitionServiceConfiguration(arguments))
        request = RecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(
                    language="pt-BR",
                    sample_rate_hz=8000,
                    audio_encoding="PCM",
                    enable_formatting=False,
                ),
                resource=RecognitionResource(topic="GENERIC"),
            ),
            audio=b"0000",
        )
        self.assertEqual(
            service.eventHandle(request).transcription,
            DEFAULT_CORRECT_PORTUGUESE_MESSAGE,
        )

    def testRecognizeRequestSink(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        response = TranscriptionResult(
            transcription="hello world",
            score=1.0,
            wordTimestamps=[(1.0, 1.5), (1.8, 2.6)],
        )
        result = {
            "alternatives": [
                {
                    "transcript": "hello world",
                    "confidence": 1.0,
                    "words": [
                        {
                            "start_time": {"seconds": 1},
                            "end_time": {"seconds": 1, "nanos": 500000000},
                            "word": "hello",
                            "confidence": 1.0,
                        },
                        {
                            "start_time": {"seconds": 1, "nanos": 800000000},
                            "end_time": {"seconds": 2, "nanos": 600000000},
                            "word": "world",
                            "confidence": 1.0,
                        },
                    ],
                }
            ],
            "duration": {},
            "end_time": {"seconds": 0, "nanos": 0},
        }
        self.assertEqual(service.eventSink(response), RecognizeResponse(**result))

    def testRecognizeRequestSinkNoFrames(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        response = TranscriptionResult(
            transcription="",
            score=1.0,
            wordTimestamps=[],
        )
        result = {
            "alternatives": [
                {
                    "transcript": "",
                    "confidence": 1.0,
                    "words": [],
                }
            ],
            "duration": {},
            "end_time": {"seconds": 0, "nanos": 0},
        }
        self.assertEqual(service.eventSink(response), RecognizeResponse(**result))

    def testRecognizeFormatter(self):
        arguments = MockArguments()
        arguments.language = Language.ES
        arguments.vocabulary = None
        service = RecognizerService(
            MockRecognitionServiceConfiguration(arguments),
            MockFormatter(FORMATTED_SPANISH_MESSAGE),
        )
        request = RecognizeRequest(
            config=RecognitionConfig(
                parameters=RecognitionParameters(
                    language="es",
                    sample_rate_hz=8000,
                    audio_encoding="PCM",
                ),
                resource=RecognitionResource(topic="GENERIC"),
            ),
            audio=b"0000",
        )
        request.config.parameters.enable_formatting = True
        self.assertEqual(
            service.eventHandle(request).transcription,
            FORMATTED_SPANISH_MESSAGE,
        )

        request.config.parameters.enable_formatting = False
        self.assertEqual(
            service.eventHandle(request).transcription,
            DEFAULT_CORRECT_SPANISH_MESSAGE,
        )

    def testResponseParameters(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        transcription = "".join(
            random.choices(string.ascii_letters + string.digits, k=16)
        )
        response = service.eventSink(
            TranscriptionResult(
                transcription=transcription, score=1.0, wordTimestamps=[(1.0, 1.5)]
            )
        )
        self.assertEqual(len(response.alternatives), 1)
        self.assertEqual(response.alternatives[0].transcript, transcription)
        self.assertEqual(response.alternatives[0].confidence, 1.0)
        self.assertEqual(len(response.alternatives[0].words), 1)
        self.assertEqual(response.alternatives[0].words[0].start_time.seconds, 1)
        self.assertEqual(response.alternatives[0].words[0].end_time.seconds, 1)
        self.assertEqual(response.alternatives[0].words[0].end_time.nanos, 500000000)

    def testStreamingResponseParameters(self):
        service = RecognizerService(MockRecognitionServiceConfiguration())
        transcription = "".join(
            random.choices(string.ascii_letters + string.digits, k=16)
        )
        innerRecognizeResponse = service.eventSink(
            TranscriptionResult(
                transcription=transcription, score=1.0, wordTimestamps=[(1.0, 1.5)]
            ),
            Duration(seconds=1, nanos=0),
        )
        streamingResponse = StreamingRecognizeResponse(
            results=StreamingRecognitionResult(
                alternatives=innerRecognizeResponse.alternatives,
            )
        )
        self.assertEqual(len(streamingResponse.results.alternatives), 1)
        self.assertEqual(
            streamingResponse.results.alternatives[0].transcript, transcription
        )
        self.assertEqual(streamingResponse.results.alternatives[0].confidence, 1.0)

    def testAudioDuration(self):
        arguments = MockArguments()
        arguments.language = Language.EN_US
        arguments.vocabulary = None
        service = RecognizerService(MockRecognitionServiceConfiguration(arguments))

        config16 = RecognitionConfig(
            parameters=RecognitionParameters(sample_rate_hz=16000)
        )
        config1 = RecognitionConfig(parameters=RecognitionParameters(sample_rate_hz=1))

        request = RecognizeRequest(audio=b"", config=config16)
        duration = service.calculateAudioDuration(request)
        self.assertEqual(duration.seconds, 0)
        self.assertEqual(duration.nanos, 0)

        request = RecognizeRequest(audio=b"0124", config=config16)
        duration = service.calculateAudioDuration(request)
        self.assertEqual(duration.seconds, 0)
        self.assertEqual(duration.nanos, 125000)

        request = RecognizeRequest(audio=b"12345678901234567890", config=config16)
        duration = service.calculateAudioDuration(request)
        self.assertEqual(duration.seconds, 0)
        self.assertEqual(duration.nanos, 625000)

        request = RecognizeRequest(audio=b"0124", config=config1)
        duration = service.calculateAudioDuration(request)
        self.assertEqual(duration.seconds, 2)
        self.assertEqual(duration.nanos, 0)

        with self.assertRaises(ZeroDivisionError):
            request = RecognizeRequest(
                audio=b"0124",
                config=RecognitionConfig(
                    parameters=RecognitionParameters(sample_rate_hz=0)
                ),
            )
            service.calculateAudioDuration(request)

    def testRecognizeFormatterESNumbers(self):
        arguments = MockArguments()
        arguments.language = Language.ES
        arguments.vocabulary = None
        formatter = FormatterFactory.createFormatter(
            os.path.join(
                os.getenv("MODELS_PATH", "models"),
                "formatter/format-model.es-es-1.1.0.fm",
            ),
            Language.ES,
        )
        service = RecognizerService(
            MockRecognitionServiceConfiguration(arguments), formatter=formatter
        )
        self.assertEqual(
            service.formatWords(
                "mi dni es siete siete uno uno cuatro tres seis ocho zeta"
            ),
            "Mi dni es 77114368-Z",
        )

    def testRecognizeFormatterESEmails(self):
        arguments = MockArguments()
        arguments.language = Language.ES
        arguments.vocabulary = None
        formatter = FormatterFactory.createFormatter(
            os.path.join(
                os.getenv("MODELS_PATH", "models"),
                "formatter/format-model.es-es-1.1.0.fm",
            ),
            Language.ES,
        )
        service = RecognizerService(
            MockRecognitionServiceConfiguration(arguments), formatter=formatter
        )
        self.assertEqual(
            service.formatWords("mi email es test arroba verbio punto com"),
            "Mi email es Test@verbio.com",
        )

    def testRecognizeFormatterESPunctuation(self):
        arguments = MockArguments()
        arguments.language = Language.ES
        arguments.vocabulary = None
        formatter = FormatterFactory.createFormatter(
            os.path.join(
                os.getenv("MODELS_PATH", "models"),
                "formatter/format-model.es-es-1.1.0.fm",
            ),
            Language.ES,
        )
        service = RecognizerService(
            MockRecognitionServiceConfiguration(arguments), formatter=formatter
        )
        self.assertEqual(
            service.formatWords("en qué puedo ayudarle"),
            "¿En qué puedo ayudarle?",
        )

    def testRecognizeFormatterESCapitalization(self):
        arguments = MockArguments()
        arguments.language = Language.ES
        arguments.vocabulary = None
        formatter = FormatterFactory.createFormatter(
            os.path.join(
                os.getenv("MODELS_PATH", "models"),
                "formatter/format-model.es-es-1.1.0.fm",
            ),
            Language.ES,
        )
        service = RecognizerService(
            MockRecognitionServiceConfiguration(arguments), formatter=formatter
        )
        self.assertEqual(
            service.formatWords("mi nombre es maría"),
            "Mi nombre es María...",
        )

    def testRecognizeFormatterEN_USNumbers(self):
        arguments = MockArguments()
        arguments.language = Language.EN_US
        arguments.vocabulary = None
        formatter = FormatterFactory.createFormatter(
            os.path.join(
                os.getenv("MODELS_PATH", "models"),
                "formatter/format-model.en-us-1.0.1.fm",
            ),
            Language.EN_US,
        )
        service = RecognizerService(
            MockRecognitionServiceConfiguration(arguments), formatter=formatter
        )
        self.assertEqual(
            service.formatWords("three million dot fourteen"),
            "3,000,000.14.",
        )

    def testRecognizeFormatterEN_USEmails(self):
        arguments = MockArguments()
        arguments.language = Language.EN_US
        arguments.vocabulary = None
        formatter = FormatterFactory.createFormatter(
            os.path.join(
                os.getenv("MODELS_PATH", "models"),
                "formatter/format-model.en-us-1.0.1.fm",
            ),
            Language.EN_US,
        )
        service = RecognizerService(
            MockRecognitionServiceConfiguration(arguments), formatter=formatter
        )
        self.assertEqual(
            service.formatWords("my email address john at gmail dot com"),
            "My email address John@gmail.com.",
        )

    def testRecognizeFormatterEN_USPunctuation(self):
        arguments = MockArguments()
        arguments.language = Language.EN_US
        arguments.vocabulary = None
        formatter = FormatterFactory.createFormatter(
            os.path.join(
                os.getenv("MODELS_PATH", "models"),
                "formatter/format-model.en-us-1.0.1.fm",
            ),
            Language.EN_US,
        )
        service = RecognizerService(
            MockRecognitionServiceConfiguration(arguments), formatter=formatter
        )
        self.assertEqual(
            service.formatWords("how are you"),
            "How are you?",
        )

    def testRecognizeFormatterEN_USCapitalization(self):
        arguments = MockArguments()
        arguments.language = Language.EN_US
        arguments.vocabulary = None
        formatter = FormatterFactory.createFormatter(
            os.path.join(
                os.getenv("MODELS_PATH", "models"),
                "formatter/format-model.en-us-1.0.1.fm",
            ),
            Language.EN_US,
        )
        service = RecognizerService(
            MockRecognitionServiceConfiguration(arguments), formatter=formatter
        )
        self.assertEqual(
            service.formatWords("my name is john"),
            "My name is John.",
        )

    def testRecognizeFormatterPT_BRNumbers(self):
        arguments = MockArguments()
        arguments.language = Language.PT_BR
        arguments.vocabulary = None
        formatter = FormatterFactory.createFormatter(
            os.path.join(
                os.getenv("MODELS_PATH", "models"),
                "formatter/format-model.pt-br-1.1.1.fm",
            ),
            Language.PT_BR,
        )
        service = RecognizerService(
            MockRecognitionServiceConfiguration(arguments), formatter=formatter
        )
        self.assertEqual(
            service.formatWords("três mil duzentos e quarenta e cinco"),
            "3.245.",
        )

    def testRecognizeFormatterPT_BREmails(self):
        arguments = MockArguments()
        arguments.language = Language.PT_BR
        arguments.vocabulary = None
        formatter = FormatterFactory.createFormatter(
            os.path.join(
                os.getenv("MODELS_PATH", "models"),
                "formatter/format-model.pt-br-1.1.1.fm",
            ),
            Language.PT_BR,
        )
        service = RecognizerService(
            MockRecognitionServiceConfiguration(arguments), formatter=formatter
        )
        self.assertEqual(
            service.formatWords("meu email é joão at domínio dot com"),
            "Meu email é João@domínio.com",
        )

    def testRecognizeFormatterPT_BRPunctuation(self):
        arguments = MockArguments()
        arguments.language = Language.PT_BR
        arguments.vocabulary = None
        formatter = FormatterFactory.createFormatter(
            os.path.join(
                os.getenv("MODELS_PATH", "models"),
                "formatter/format-model.pt-br-1.1.1.fm",
            ),
            Language.PT_BR,
        )
        service = RecognizerService(
            MockRecognitionServiceConfiguration(arguments), formatter=formatter
        )
        self.assertEqual(
            service.formatWords("como vai que eu possa ajudar"),
            "Como vai que eu possa ajudar?",
        )

    def testRecognizeFormatterPT_BRCapitalization(self):
        arguments = MockArguments()
        arguments.language = Language.PT_BR
        arguments.vocabulary = None
        formatter = FormatterFactory.createFormatter(
            os.path.join(
                os.getenv("MODELS_PATH", "models"),
                "formatter/format-model.pt-br-1.1.1.fm",
            ),
            Language.PT_BR,
        )
        service = RecognizerService(
            MockRecognitionServiceConfiguration(arguments), formatter=formatter
        )
        self.assertEqual(
            service.formatWords("meu nome é joão"),
            "Meu nome é João",
        )
