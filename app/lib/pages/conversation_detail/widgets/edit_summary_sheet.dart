import 'package:flutter/material.dart';

import 'package:omi/utils/l10n_extensions.dart';

void showEditSummaryBottomSheet(
  BuildContext context, {
  required String overview,
  required Function(String newOverview) onSave,
  VoidCallback? onDismissed,
}) {
  showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    backgroundColor: Colors.grey.shade900,
    shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(16))),
    builder: (context) => _EditSummarySheet(overview: overview, onSave: onSave),
  ).whenComplete(() => onDismissed?.call());
}

class _EditSummarySheet extends StatefulWidget {
  final String overview;
  final Function(String newOverview) onSave;

  const _EditSummarySheet({required this.overview, required this.onSave});

  @override
  State<_EditSummarySheet> createState() => _EditSummarySheetState();
}

class _EditSummarySheetState extends State<_EditSummarySheet> {
  late final TextEditingController _controller;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(text: widget.overview);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _save() {
    final newOverview = _controller.text.trim();
    if (newOverview.isNotEmpty && newOverview != widget.overview) {
      widget.onSave(newOverview);
    }
    Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: MediaQuery.of(context).viewInsets,
      child: SingleChildScrollView(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Center(
                child: Container(
                  width: 40,
                  height: 4,
                  decoration: BoxDecoration(color: Colors.grey.shade600, borderRadius: BorderRadius.circular(2)),
                ),
              ),
              const SizedBox(height: 16),
              Text(
                context.l10n.summary,
                style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.w600),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _controller,
                autofocus: true,
                maxLines: null,
                minLines: 6,
                maxLength: 10000,
                style: const TextStyle(color: Colors.white, fontSize: 15, height: 1.5),
                decoration: InputDecoration(
                  filled: true,
                  fillColor: Colors.grey.shade800,
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
                  contentPadding: const EdgeInsets.all(14),
                  counterStyle: TextStyle(color: Colors.grey.shade500),
                ),
              ),
              const SizedBox(height: 16),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: _save,
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.white,
                    foregroundColor: Colors.black,
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                  ),
                  child: Text(context.l10n.save, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
