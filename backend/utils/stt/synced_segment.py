"""Transcription helper for synced audio segments.

On a self-hosted backend, synced segments are staged in a *private* GCS
emulator (fake-gcs) whose download URLs are not publicly routable. Deepgram's
pre-recorded-by-URL API rejects such URLs:

    DeepgramApiError: Could not determine if URL for media download is
    publicly routable. (Status: 400)

So we transcribe synced segments from their raw bytes instead of handing the
STT provider a signed URL. The backend can always read the segment locally
(it was just decoded to disk before upload), so no storage round-trip is
needed for transcription.
"""

from typing import List, Optional, Sequence, Tuple, Union

from utils.stt.pre_recorded import prerecorded_from_bytes


def transcribe_segment_bytes(
    audio_bytes: bytes,
    *,
    language: Optional[str] = None,
    keywords: Optional[Sequence[str]] = None,
    return_language: bool = True,
) -> Union[List[dict], Tuple[List[dict], str]]:
    """Transcribe an in-memory WAV segment via the bytes-based prerecorded path.

    Diarization is always on so segments carry speaker labels, matching the
    behaviour of the previous URL-based call in ``process_segment``.
    """
    return prerecorded_from_bytes(
        audio_bytes,
        language=language,
        keywords=keywords,
        return_language=return_language,
        diarize=True,
    )
