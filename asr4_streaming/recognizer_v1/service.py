import abc
import grpc
import toml
import asyncio
import logging
import numpy as np
from typing import List, Optional
from datetime import timedelta
from dataclasses import dataclass
from asyncio import Event

from .types import RecognizerServicer
from .types import RecognizeRequest
from .types import StreamingRecognizeRequest
from .types import RecognitionConfig
from .types import RecognitionParameters
from .types import RecognitionResource
from .types import RecognizeResponse
from .types import StreamingRecognizeResponse
from .types import StreamingRecognitionResult
from .types import RecognitionAlternative
from .types import Duration
from .types import WordInfo
from .types import SampleRate
from .types import AudioEncoding

from google.protobuf.reflection import GeneratedProtocolMessageType

from asr4_engine.data_classes import Signal, Segment
from asr4.engines.wav2vec.v1.engine_types import Language
from asr4_engine.data_classes.transcription import WordTiming
from asr4.engines.wav2vec import Wav2VecEngineFactory, Wav2VecASR4EngineOnlineHandler


@dataclass
class TranscriptionResult:
    transcription: str
    score: float
    words: List[WordTiming]


class SourceSinkService(abc.ABC):
    def eventSource(
        self,
        _request: GeneratedProtocolMessageType,
    ) -> None:
        raise NotImplementedError()

    def eventHandle(self, _request: GeneratedProtocolMessageType) -> str:
        raise NotImplementedError()

    def eventSink(self, _response: str) -> GeneratedProtocolMessageType:
        raise NotImplementedError()


class RecognizerService(RecognizerServicer, SourceSinkService):
    def __init__(self, config: str) -> None:
        self.config = config
        self.logger = logging.getLogger("ASR4")
        tomlConfiguration = toml.load(self.config)
        logging.debug(f"Toml configuration file: {self.config}")
        logging.debug(f"Toml configuration: {tomlConfiguration}")
        self._languageCode = tomlConfiguration.get("global", {}).get(
            "language", "en-US"
        )
        self._language = Language.parse(self._languageCode)
        self._engine = self.initializeEngine(tomlConfiguration, self._languageCode)
        logging.info(f"Recognizer supported language is: {self._languageCode}")

    def initializeEngine(
        self, tomlConfiguration: dict, languageCode: str
    ) -> Wav2VecEngineFactory:
        factory = Wav2VecEngineFactory()
        engine = factory.create_engine()
        engine.initialize(config=toml.dumps(tomlConfiguration), language=languageCode)
        return engine

    async def Recognize(
        self,
        request: RecognizeRequest,
        _context: grpc.aio.ServicerContext,
    ) -> RecognizeResponse:
        """
        Send audio as bytes and receive the transcription of the audio.
        """
        self.eventSource(request)
        duration = self.calculateAudioDuration(
            request.audio,
            request.config.parameters.audio_encoding,
            request.config.parameters.sample_rate_hz,
        )
        self.logger.info(
            "Received request "
            f"[language={request.config.parameters.language}] "
            f"[sample_rate={request.config.parameters.sample_rate_hz}] "
            f"[formatting={request.config.parameters.enable_formatting}] "
            f"[length={len(request.audio)}] "
            f"[duration={duration.ToTimedelta().total_seconds()}] "
            f"[topic={RecognitionResource.Model.Name(request.config.resource.topic)}]"
        )
        response = self.eventHandle(request)
        response = self.eventSink(response, duration, duration)
        self.logger.info(f"Recognition result: '{response.alternatives[0].transcript}'")
        return response

    async def StreamingRecognize(
        self,
        request_iterator: StreamingRecognizeRequest,
        context: grpc.aio.ServicerContext,
    ) -> StreamingRecognizeResponse:
        """
        Send audio as a stream of bytes and receive the transcription of the audio through another stream.
        """
        handler: Optional[Wav2VecASR4EngineOnlineHandler] = None
        config: Optional[RecognitionConfig] = RecognitionConfig()
        streamHasEnded = Event()

        async for request in request_iterator:
            if request.HasField("config"):
                self.logger.info(
                    "Received streaming request "
                    f"[language={request.config.parameters.language}] "
                    f"[sample_rate={request.config.parameters.sample_rate_hz}] "
                    f"[formatting={request.config.parameters.enable_formatting}] "
                    f"[topic={RecognitionResource.Model.Name(request.config.resource.topic)}]"
                )
                config.CopyFrom(request.config)
                self._validateConfig(config)
                handler = self._engine.getRecognizerHandler(
                    language=config.parameters.language,
                    formatter=config.parameters.enable_formatting,
                )
                listenerTask = asyncio.create_task(
                    self.listenForTranscription(handler, context, streamHasEnded)
                )
            if request.HasField("audio"):
                duration = self.calculateAudioDuration(
                    request.audio,
                    config.parameters.audio_encoding,
                    config.parameters.sample_rate_hz,
                )
                self.logger.info(
                    f"Received partial audio "
                    f"[length={len(request.audio)}] "
                    f"[duration={duration.ToTimedelta().total_seconds()}] "
                )
                self._validateAudio(request.audio)
                await self.__sendAudioChunk(
                    request.audio, config.parameters.sample_rate_hz
                )
        handler.notifyEndOfAudio()
        streamHasEnded.set()
        finalResponse = await listenerTask
        yield finalResponse
        return

    async def listenForTranscription(
        self,
        handler: Wav2VecASR4EngineOnlineHandler,
        context: grpc.aio.ServicerContext,
        streamHasEnded: Event,
    ):
        totalDuration = Duration()
        response: Optional[RecognizeResponse] = None
        # TODO: listenForCompleteAudio should tell me if the partialResult is the last one
        async for partialResult in handler.listenForCompleteAudio():
            if response and streamHasEnded.is_set():
                await context.write(self.buildPartialResult(response))
            partialTranscriptionResult = TranscriptionResult(
                transcription=partialResult.text,
                score=self.calculateAverageScore(partialResult.segments),
                words=self.extractWords(partialResult.segments),
            )
            # TODO: This should come from the transcription
            duration = duration.FromTimedelta(td=timedelta(seconds=10))
            totalDuration = RecognizerService.addAudioDuration(totalDuration, duration)
            response = self.eventSink(
                partialTranscriptionResult, duration, totalDuration
            )
            if not streamHasEnded.is_set():
                await self.sendPartialResult(response, context)
                response = None
        return self.buildPartialResult(response, isFinal=True)

    def buildPartialResult(
        self, response: RecognizeResponse, isFinal: bool = False
    ) -> StreamingRecognizeResponse:
        self.logger.info(f"Recognition result: '{response.alternatives[0].transcript}'")
        return StreamingRecognizeResponse(
            results=StreamingRecognitionResult(
                alternatives=response.alternatives,
                end_time=response.end_time,
                duration=response.duration,
                is_final=isFinal,
            )
        )

    def eventSource(
        self,
        request: RecognizeRequest,
    ) -> None:
        self._validateConfig(request.config)
        self._validateAudio(request.audio)

    def _validateConfig(
        self,
        config: RecognitionConfig,
    ) -> None:
        self._validateParameters(config.parameters)
        self._validateResource(config.resource)

    def _validateParameters(
        self,
        parameters: RecognitionParameters,
    ) -> None:
        if not Language.check(parameters.language):
            raise ValueError(
                f"Invalid value '{parameters.language}' for language parameter"
            )
        if Language.parse(parameters.language) != self._language:
            raise ValueError(
                f"Invalid language '{parameters.language}'. Only '{self._language}' is supported."
            )
        if not SampleRate.check(parameters.sample_rate_hz):
            raise ValueError(
                f"Invalid value '{parameters.sample_rate_hz}' for sample_rate_hz parameter"
            )
        if not AudioEncoding.check(parameters.audio_encoding):
            raise ValueError(
                f"Invalid value '{parameters.audio_encoding}' for audio_encoding parameter"
            )

    def _validateResource(
        self,
        resource: RecognitionResource,
    ) -> None:
        try:
            RecognitionResource.Model.Name(resource.topic)
        except:
            raise ValueError(f"Invalid value '{resource.topic}' for topic resource")

    def _validateAudio(
        self,
        audio: bytes,
    ) -> None:
        if len(audio) == 0:
            raise ValueError(f"Empty value for audio")

    async def sendAudioChunk(self, audio: bytes, sampleRate: int):
        await self._handler.sendAudioChunk(
            Signal(np.frombuffer(audio, dtype=np.int16), sampleRate)
        )

    def eventHandle(self, request: RecognizeRequest) -> TranscriptionResult:
        language = Language.parse(request.config.parameters.language)
        sample_rate_hz = request.config.parameters.sample_rate_hz
        if language == self._language:
            result = self._engine.recognize(
                Signal(np.frombuffer(request.audio, dtype=np.int16), sample_rate_hz),
                language=self._languageCode,
                formatter=request.config.parameters.enable_formatting,
            )
            return TranscriptionResult(
                transcription=result.text,
                score=self.calculateAverageScore(result.segments),
                words=self.extractWords(result.segments),
            )

        else:
            raise ValueError(
                f"Invalid language '{language}'. Only '{self._language}' is supported."
            )

    def calculateAverageScore(self, segments: List[Segment]) -> float:
        acummScore = 0.0
        for segment in segments:
            acummScore += segment.avg_logprob
        return acummScore / len(segments)

    def extractWords(self, segments: List[Segment]) -> List[WordTiming]:
        words = []
        for segment in segments:
            words.extend(segment.words)
        return words

    def eventSink(
        self,
        response: TranscriptionResult,
        duration: Duration = Duration(seconds=0, nanos=0),
        endTime: Duration = Duration(seconds=0, nanos=0),
    ) -> RecognizeResponse:
        def getWord(word: WordTiming) -> WordInfo:
            wordInfo = WordInfo(
                start_time=Duration(),
                end_time=Duration(),
                word=word.word,
                confidence=word.probability,
            )
            wordInfo.start_time.FromTimedelta(td=timedelta(seconds=word.start))
            wordInfo.end_time.FromTimedelta(td=timedelta(seconds=word.end))
            return wordInfo

        if len(response.words) > 0:
            words = [getWord(word) for word in response.words]
        else:
            words = []

        alternative = RecognitionAlternative(
            transcript=response.transcription, confidence=response.score, words=words
        )
        return RecognizeResponse(
            alternatives=[alternative],
            end_time=endTime,
            duration=duration,
        )

    def calculateAudioDuration(
        self, audio: bytes, audioEncoding: int, sampleRate: int
    ) -> Duration:
        duration = Duration()
        audioEncoding = AudioEncoding.parse(audioEncoding)
        # We only support 1 channel
        bytesPerFrame = audioEncoding.getSampleSizeInBytes() * 1
        framesNumber = len(audio) / bytesPerFrame
        td = timedelta(seconds=(framesNumber / sampleRate))
        duration.FromTimedelta(td=td)
        return duration

    @staticmethod
    def addAudioDuration(a: Duration, b: Duration) -> Duration:
        duration = Duration()
        total = a.ToTimedelta().total_seconds() + b.ToTimedelta().total_seconds()
        duration.FromTimedelta(td=timedelta(seconds=total))
        return duration
