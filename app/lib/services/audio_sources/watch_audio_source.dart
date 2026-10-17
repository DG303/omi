import 'package:omi/backend/schema/bt_device/bt_device.dart';
import 'package:omi/services/audio_sources/audio_source.dart';
import 'package:omi/services/audio_sources/ble_device_source.dart';
import 'package:omi/services/audio_sources/phone_mic_source.dart';

/// Audio source for the Apple Watch.
///
/// Watch chunks are PCM16 16kHz mono with the same 3-byte dummy header on every chunk
/// (AppDelegate.handleAudioChunk), so the header can't be a sync key. Strip it and let a
/// contained PhoneMicSource re-frame into 320-byte frames with index sync keys.
/// Contains rather than extends PhoneMicSource: capture_provider's `is PhoneMicSource`
/// checks drive phone-mic restart logic that must not fire for the watch.
class WatchAudioSource implements AudioSource {
  @override
  final String deviceId;

  @override
  final String deviceModel;

  final PhoneMicSource _framer = PhoneMicSource();

  WatchAudioSource({required this.deviceId, required this.deviceModel});

  @override
  BleAudioCodec get codec => BleAudioCodec.pcm16;

  @override
  List<WalFrame> processBytes(List<int> rawBytes) => _framer.processBytes(getSocketPayload(rawBytes));

  @override
  List<int> getSocketPayload(List<int> rawBytes) =>
      rawBytes.length > BleDeviceSource.headerSize ? rawBytes.sublist(BleDeviceSource.headerSize) : const [];

  @override
  List<WalFrame> flush() => _framer.flush();
}
