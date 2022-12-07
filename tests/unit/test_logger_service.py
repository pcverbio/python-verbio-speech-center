import multiprocessing
import unittest
import logging
import re


from asr4.recognizer_v1 import LoggerService, Logger, LoggerQueue


class TestLoggerService(unittest.TestCase):
    def testServiceStops(self):
        service = LoggerService("INFO", "TestASR4")
        service.configureGlobalLogger()
        service.stop()

    def testDefaults(self):
        self.assertEqual(LoggerService.getDefaultLogLevel(), "ERROR")

    def testLogger(self):
        queue = multiprocessing.Queue(-1)
        loggerName = "TestASR4"
        message = "message"
        logger = Logger(loggerName, logging.INFO, queue)
        self.assertEqual(0, queue.qsize())
        logger.info(message)
        self.assertEqual(1, queue.qsize())
        record = queue.get(timeout=5)
        self.assertEqual(record.name, loggerName)
        self.assertEqual(record.message, message)
        self.assertEqual(0, queue.qsize())
        logger.debug(message)
        self.assertEqual(0, queue.qsize())

    def testLoggerQueue(self):
        queue = multiprocessing.Queue(-1)
        loggerName = "TestASR4"
        message = "message"
        loggerQueue = LoggerQueue(loggerName, logging.INFO, queue)
        self.assertEqual(type(loggerQueue.getLogger()), Logger)
        loggerQueue.configureGlobalLogger()
        loggerQueue.getLogger().info(message)
        self.assertEqual(1, queue.qsize())
        record = queue.get(timeout=5)
        self.assertEqual(record.name, loggerName)
        self.assertEqual(record.message, message)

    def testValidateLogLevel(self):
        self.assertEqual(LoggerService.getDefaultLogLevel(), "ERROR")
        loggerService = LoggerService()
        self.assertEqual(loggerService.validateLogLevel("INFO"), logging.INFO)
        self.assertEqual(loggerService.validateLogLevel("WARNING"), logging.WARNING)
        self.assertEqual(loggerService.validateLogLevel("WARN"), logging.WARNING)
        self.assertEqual(
            loggerService.validateLogLevel("xxxxx"),
            loggerService.validateLogLevel(LoggerService.getDefaultLogLevel()),
        )
        loggerService.stop()

    def testValidOptions(self):
        self.assertEqual(
            LoggerService.getLogLevelOptions(),
            ["ERROR", "WARNING", "WARN", "INFO", "DEBUG", "TRACE"],
        )
