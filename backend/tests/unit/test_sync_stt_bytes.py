"""Self-host sync transcription must send audio BYTES to the STT provider,
not a (private, non-routable) GCS-emulator URL.

Background: on a self-hosted backend, synced audio segments live in a private
fake-gcs bucket. The upstream sync path handed Deepgram a signed URL to that
bucket, which Deepgram (a cloud service) cannot fetch:

    DeepgramApiError: Could not determine if URL for media download is
    publicly routable. (Status: 400)

Every synced segment therefore failed. The fix routes synced-segment
transcription through the bytes-based prerecorded path instead.

These tests stub ``utils.stt.pre_recorded`` so they run without Deepgram /
storage / firebase deps (same isolation pattern as test_sync_v2).
"""

import os
import sys
import types
import importlib
import unittest.mock as mock


def _install_pre_recorded_stub():
    """Replace utils.stt.pre_recorded with a stub exposing a spy
    prerecorded_from_bytes, and a prerecorded (URL) that explodes if used."""
    pkg_utils = sys.modules.setdefault('utils', types.ModuleType('utils'))
    pkg_stt = sys.modules.setdefault('utils.stt', types.ModuleType('utils.stt'))
    pkg_utils.stt = pkg_stt

    stub = types.ModuleType('utils.stt.pre_recorded')
    stub.prerecorded_from_bytes = mock.MagicMock(return_value=(['word'], 'en'))

    def _no_url(*a, **k):
        raise AssertionError('synced segment transcription must not use the URL path')

    stub.prerecorded = _no_url
    sys.modules['utils.stt.pre_recorded'] = stub
    pkg_stt.pre_recorded = stub
    return stub


def _load_synced_segment():
    stub = _install_pre_recorded_stub()
    sys.modules.pop('utils.stt.synced_segment', None)
    mod_path = os.path.join(os.path.dirname(__file__), '..', '..', 'utils', 'stt', 'synced_segment.py')
    spec = importlib.util.spec_from_file_location('utils.stt.synced_segment', mod_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, stub


class TestTranscribeSegmentBytes:
    def test_sends_bytes_to_prerecorded_from_bytes(self):
        module, stub = _load_synced_segment()
        result = module.transcribe_segment_bytes(
            b'RIFFfakewav', language='multi', keywords=['Omi'], return_language=True
        )
        # Returns whatever the bytes provider returned
        assert result == (['word'], 'en')
        # Called the bytes provider exactly once with our bytes + params
        stub.prerecorded_from_bytes.assert_called_once()
        args, kwargs = stub.prerecorded_from_bytes.call_args
        passed_bytes = args[0] if args else kwargs.get('audio_bytes')
        assert passed_bytes == b'RIFFfakewav'
        assert kwargs.get('language') == 'multi'
        assert kwargs.get('keywords') == ['Omi']
        assert kwargs.get('return_language') is True
        # diarization on for speaker labels
        assert kwargs.get('diarize') is True


class TestProcessSegmentWiring:
    """Structural: process_segment must use the bytes path, not the signed URL."""

    @staticmethod
    def _read_sync_source():
        p = os.path.join(os.path.dirname(__file__), '..', '..', 'routers', 'sync.py')
        with open(p, encoding='utf-8') as f:
            return f.read()

    def test_process_segment_calls_bytes_transcription(self):
        src = self._read_sync_source()
        seg = src[src.index('def process_segment(') :]
        seg = seg[: seg.index('\ndef ', 1)]
        assert 'transcribe_segment_bytes(' in seg, 'process_segment must call transcribe_segment_bytes'

    def test_process_segment_does_not_feed_url_to_stt(self):
        src = self._read_sync_source()
        seg = src[src.index('def process_segment(') :]
        seg = seg[: seg.index('\ndef ', 1)]
        # The old, broken pattern: handing the signed URL to the prerecorded URL API.
        assert (
            'get_syncing_file_temporal_signed_url(' not in seg
        ), 'process_segment must not build a (non-routable) signed URL for STT'
