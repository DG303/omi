import 'package:flutter_test/flutter_test.dart';
import 'package:omi/backend/schema/bt_device/bt_device.dart';
import 'package:omi/services/audio_sources/audio_source.dart';
import 'package:omi/services/audio_sources/phone_mic_source.dart';
import 'package:omi/services/audio_sources/watch_audio_source.dart';

List<int> watchChunk(int audioLen, int fill) => [0, 0, 0, ...List<int>.filled(audioLen, fill)];

void main() {
  group('WatchAudioSource', () {
    test('metadata', () {
      final s = WatchAudioSource(deviceId: 'watch-1', deviceModel: 'Apple Watch');
      expect(s.codec, BleAudioCodec.pcm16);
      expect(s.deviceId, 'watch-1');
      expect(s.deviceModel, 'Apple Watch');
      expect(s, isNot(isA<PhoneMicSource>()));
    });

    test('strips header, emits exact 320-byte frames, carries remainder', () {
      final s = WatchAudioSource(deviceId: 'w', deviceModel: 'm');

      // 500 audio bytes -> 1 frame, 180 left over. Header bytes must not leak in.
      final f1 = s.processBytes(watchChunk(500, 7));
      expect(f1.length, 1);
      expect(f1[0].payload.length, PhoneMicSource.frameSize);
      expect(f1[0].payload.every((b) => b == 7), isTrue);
      expect(f1[0].syncKey, FrameSyncKey.fromIndex(0));

      // 180 + 460 = 640 -> 2 frames, 0 left over. First frame spans both chunks.
      final f2 = s.processBytes(watchChunk(460, 9));
      expect(f2.length, 2);
      expect(f2[0].payload.sublist(0, 180).every((b) => b == 7), isTrue);
      expect(f2[0].payload.sublist(180).every((b) => b == 9), isTrue);
      expect(f2[1].payload.every((b) => b == 9), isTrue);
      expect(f2.map((f) => f.syncKey).toList(), [FrameSyncKey.fromIndex(1), FrameSyncKey.fromIndex(2)]);

      expect(s.flush(), isEmpty);
    });

    test('flush pads the remainder into one frame', () {
      final s = WatchAudioSource(deviceId: 'w', deviceModel: 'm');
      s.processBytes(watchChunk(100, 5));
      final f = s.flush();
      expect(f.length, 1);
      expect(f[0].payload.length, PhoneMicSource.frameSize);
      expect(f[0].payload.sublist(0, 100).every((b) => b == 5), isTrue);
      expect(f[0].payload.sublist(100).every((b) => b == 0), isTrue);
    });

    test('header-only or short chunk yields nothing', () {
      final s = WatchAudioSource(deviceId: 'w', deviceModel: 'm');
      expect(s.processBytes([0, 0, 0]), isEmpty);
      expect(s.processBytes([0, 0]), isEmpty);
      expect(s.flush(), isEmpty);
    });

    test('getSocketPayload strips the header', () {
      final s = WatchAudioSource(deviceId: 'w', deviceModel: 'm');
      expect(s.getSocketPayload([0, 0, 0, 1, 2, 3]), [1, 2, 3]);
      expect(s.getSocketPayload([0, 0, 0]), isEmpty);
    });
  });
}
