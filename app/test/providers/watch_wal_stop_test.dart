import 'dart:async';
import 'dart:io';

import 'package:connectivity_plus_platform_interface/connectivity_plus_platform_interface.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:omi/backend/preferences.dart';
import 'package:omi/backend/schema/bt_device/bt_device.dart';
import 'package:omi/backend/schema/conversation.dart';
import 'package:omi/gen/pigeon_communicator.g.dart';
import 'package:omi/providers/capture_provider.dart';
import 'package:omi/services/audio_sources/watch_audio_source.dart';
import 'package:omi/services/devices.dart';
import 'package:omi/services/devices/discovery/device_locator.dart';
import 'package:omi/services/services.dart';
import 'package:omi/services/wals.dart';
import 'package:omi/utils/wal_file_manager.dart';

class _OfflineConnectivity extends ConnectivityPlatform {
  @override
  Future<List<ConnectivityResult>> checkConnectivity() async => [ConnectivityResult.none];
  @override
  Stream<List<ConnectivityResult>> get onConnectivityChanged => const Stream.empty();
}

class _SavedWalListener implements IWalServiceListener {
  final saved = Completer<void>();
  @override
  void onWalUpdated() {
    if (!saved.isCompleted) saved.complete();
  }

  @override
  void onStatusChanged(WalServiceStatus status) {}
  @override
  void onWalSynced(Wal wal, {ServerConversation? conversation}) {}
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(() async {
    ConnectivityPlatform.instance = _OfflineConnectivity();
    await ServiceManager.init();
  });

  for (final stopOnWatch in [false, true]) {
    test('20 seconds offline saved on ${stopOnWatch ? "Watch stop event" : "device cleanup"}', () async {
      SharedPreferences.setMockInitialValues({});
      await SharedPreferencesUtil.init();
      final directory = await Directory.systemTemp.createTemp('watch_stop_');
      final messenger = TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
      messenger.setMockMethodCallHandler(const MethodChannel('plugins.flutter.io/path_provider'), (call) async {
        return call.method == 'getApplicationDocumentsDirectory' ? directory.path : null;
      });
      await WalFileManager.init();
      final LocalWalSyncImpl sync = ServiceManager.instance().wal.getSyncs().phone;
      sync.testWals = [];
      await sync.onAudioCodecChanged(BleAudioCodec.pcm16);
      sync.setDeviceInfo('apple-watch', 'Apple Watch');
      final provider = CaptureProvider();
      final watch = BtDevice(
          id: 'apple-watch',
          name: 'Watch',
          type: DeviceType.appleWatch,
          rssi: 0,
          locator: DeviceLocator.watchConnectivity());
      provider.updateRecordingDevice(watch);
      if (stopOnWatch) {
        for (final method in ['isWatchSessionSupported', 'isWatchPaired', 'isWatchReachable']) {
          final channel = BasicMessageChannel<Object?>(
            'dev.flutter.pigeon.omi_pigeon.WatchRecorderHostAPI.$method',
            WatchRecorderHostAPI.pigeonChannelCodec,
          );
          messenger.setMockDecodedMessageHandler<Object?>(channel, (_) async => [true]);
        }
        final devices = ServiceManager.instance().device as DeviceService;
        devices.devices.add(watch);
        final connection = await devices.ensureConnection(watch.id, force: true);
        expect(connection, isNotNull);
        addTearDown(() async => connection!.transport.dispose());
        expect(await provider.streamAudioToWs(watch.id, BleAudioCodec.pcm16), isTrue);
      }
      addTearDown(() async {
        provider.dispose();
        messenger.setMockMethodCallHandler(const MethodChannel('plugins.flutter.io/path_provider'), null);
        await directory.delete(recursive: true);
      });

      final source = WatchAudioSource(deviceId: 'apple-watch', deviceModel: 'Apple Watch');
      for (var second = 0; second < 20; second++) {
        for (final frame in source.processBytes([0, 0, 0, ...List.filled(32000, 7)])) {
          sync.onFrameCaptured(frame);
        }
      }
      if (stopOnWatch) {
        final listener = _SavedWalListener();
        ServiceManager.instance().wal.subscribe(listener, listener);
        addTearDown(() => ServiceManager.instance().wal.unsubscribe(listener));
        messenger.handlePlatformMessage(
          'dev.flutter.pigeon.omi_pigeon.WatchRecorderFlutterAPI.onRecordingStopped',
          WatchRecorderFlutterAPI.pigeonChannelCodec.encodeMessage(null),
          (_) {},
        );
        await listener.saved.future.timeout(const Duration(seconds: 5));
      } else {
        await provider.stopStreamDeviceRecording();
      }

      expect(sync.testFrames, isEmpty);
      final saved = await WalFileManager.loadWals();
      expect(saved, hasLength(1));
      expect(saved.single.seconds, 20);
      expect(saved.single.totalFrames, 2000);
      expect(saved.single.codec, BleAudioCodec.pcm16);
      expect(saved.single.device, 'apple-watch');
      expect(saved.single.status, WalStatus.miss);
      expect(saved.single.storage, WalStorage.disk);
    });
  }
}
