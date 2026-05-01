import 'package:flutter/material.dart';

import 'package:omi/pages/settings/task_integrations_page.dart';
import 'package:omi/utils/l10n_extensions.dart';
import 'package:omi/utils/platform/platform_service.dart';

/// Modal bottom sheet that lists task integrations the user can bulk-export
/// the currently selected action items to. Returns the chosen platform via
/// `Navigator.pop` so the caller can drive the export.
class ExportDestinationSheet extends StatelessWidget {
  final int selectedCount;

  const ExportDestinationSheet({super.key, required this.selectedCount});

  @override
  Widget build(BuildContext context) {
    final platforms = TaskIntegrationApp.values
        .where((p) => p.isAvailable)
        .where((p) => p != TaskIntegrationApp.appleReminders || PlatformService.isApple)
        .toList(growable: false);

    return SafeArea(
      child: Container(
        decoration: const BoxDecoration(
          color: Color(0xFF1A1A1A),
          borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
        ),
        padding: const EdgeInsets.fromLTRB(20, 12, 20, 16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Center(
              child: Container(
                width: 40,
                height: 4,
                decoration: BoxDecoration(
                  color: Colors.grey[700],
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            const SizedBox(height: 16),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 4),
              child: Text(
                context.l10n.chooseExportDestination(selectedCount),
                style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.w600),
              ),
            ),
            const SizedBox(height: 12),
            ...platforms.map((p) => _DestinationTile(platform: p)),
          ],
        ),
      ),
    );
  }
}

class _DestinationTile extends StatelessWidget {
  final TaskIntegrationApp platform;

  const _DestinationTile({required this.platform});

  @override
  Widget build(BuildContext context) {
    final logoPath = platform.logoPath;
    return InkWell(
      onTap: () => Navigator.of(context).pop(platform),
      borderRadius: BorderRadius.circular(12),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 12),
        child: Row(
          children: [
            SizedBox(
              width: 32,
              height: 32,
              child: logoPath != null
                  ? Image.asset(logoPath, fit: BoxFit.contain)
                  : Icon(platform.icon, color: platform.iconColor, size: 26),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Text(
                platform.displayName,
                style: const TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.w500),
              ),
            ),
            Icon(Icons.chevron_right, color: Colors.grey[600], size: 20),
          ],
        ),
      ),
    );
  }
}
