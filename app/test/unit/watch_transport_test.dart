import 'dart:async';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:omi/gen/pigeon_communicator.g.dart';
import 'package:omi/services/devices/transports/watch_transport.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('watch stop follows queued audio with an end-of-recording marker', () async {
    final transport = WatchTransport();
    final received = <List<int>>[];
    final subscription =
        transport.getCharacteristicStream('watch-audio-service', 'watch-audio-data').listen(received.add);
    addTearDown(() async {
      await subscription.cancel();
      await transport.dispose();
    });
    final messenger = TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
    Future<void> send(String method, Object? arguments) async {
      final done = Completer<void>();
      messenger.handlePlatformMessage(
        'dev.flutter.pigeon.omi_pigeon.WatchRecorderFlutterAPI.$method',
        WatchRecorderFlutterAPI.pigeonChannelCodec.encodeMessage(arguments),
        (_) => done.complete(),
      );
      await done.future;
    }

    await send('onAudioChunk', [
      Uint8List.fromList([0, 0, 0, 7, 8]),
      0,
      false,
      16000.0
    ]);
    await send('onRecordingStopped', null);
    await Future<void>.delayed(Duration.zero);
    expect(received, [
      [0, 0, 0, 7, 8],
      <int>[]
    ]);
  });
}
