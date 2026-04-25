import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:font_awesome_flutter/font_awesome_flutter.dart';
import 'package:provider/provider.dart';

import 'package:omi/backend/http/api/users.dart';
import 'package:omi/backend/preferences.dart';
import 'package:omi/backend/schema/conversation.dart';
import 'package:omi/backend/schema/daily_summary.dart';
import 'package:omi/pages/chat/page.dart';
import 'package:omi/pages/conversations/widgets/daily_summaries_list.dart';
import 'package:omi/pages/conversations/widgets/conversations_group_widget.dart';
import 'package:omi/pages/conversations/widgets/today_tasks_widget.dart';
import 'package:omi/pages/settings/daily_summary_detail_page.dart';
import 'package:omi/providers/conversation_provider.dart';
import 'package:omi/providers/home_provider.dart';
import 'package:omi/utils/analytics/mixpanel.dart';
import 'package:omi/utils/l10n_extensions.dart';
import 'package:omi/utils/ui_guidelines.dart';
import 'package:omi/widgets/shimmer_with_timeout.dart';

class HomeContentPage extends StatefulWidget {
  const HomeContentPage({super.key});

  @override
  State<HomeContentPage> createState() => HomeContentPageState();
}

class HomeContentPageState extends State<HomeContentPage> with AutomaticKeepAliveClientMixin {
  final ScrollController _scrollController = ScrollController();
  List<DailySummary> _recentSummaries = [];
  bool _loadingSummaries = true;

  @override
  bool get wantKeepAlive => true;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _loadSummaries());
  }

  Future<void> _loadSummaries() async {
    if (!mounted) return;
    setState(() => _loadingSummaries = true);
    final summaries = await getDailySummaries(limit: 3, offset: 0);
    if (mounted) {
      setState(() {
        _recentSummaries = summaries;
        _loadingSummaries = false;
      });
    }
  }

  void scrollToTop() {
    if (_scrollController.hasClients) {
      _scrollController.animateTo(0.0, duration: const Duration(milliseconds: 500), curve: Curves.easeOutCubic);
    }
  }

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    return Consumer<ConversationProvider>(
      builder: (context, convoProvider, child) {
        return RefreshIndicator(
          onRefresh: () async {
            HapticFeedback.mediumImpact();
            await Future.wait([
              convoProvider.getInitialConversations(),
              _loadSummaries(),
            ]);
          },
          color: Colors.deepPurpleAccent,
          backgroundColor: Colors.white,
          child: CustomScrollView(
            controller: _scrollController,
            physics: const AlwaysScrollableScrollPhysics(),
            slivers: [
              // Chat bar
              SliverToBoxAdapter(child: _buildChatBar(context)),

              // Today section
              SliverToBoxAdapter(child: _buildSectionHeader(context, context.l10n.today)),
              const SliverToBoxAdapter(child: TodayTasksWidget()),

              // Daily Recaps section
              SliverToBoxAdapter(
                child: _buildSectionHeader(
                  context,
                  context.l10n.dailyRecaps,
                  onViewAll: () {
                    if (!convoProvider.showDailySummaries) convoProvider.toggleDailySummaries();
                    context.read<HomeProvider>().setIndex(1);
                  },
                ),
              ),
              SliverToBoxAdapter(child: _buildDailyRecapsPreview(context)),

              // Conversations section
              SliverToBoxAdapter(
                child: _buildSectionHeader(
                  context,
                  context.l10n.conversations,
                  onViewAll: () => context.read<HomeProvider>().setIndex(1),
                ),
              ),
              _buildConversationsPreview(convoProvider),

              const SliverToBoxAdapter(child: SizedBox(height: 120)),
            ],
          ),
        );
      },
    );
  }

  Widget _buildChatBar(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
      child: GestureDetector(
        onTap: () {
          HapticFeedback.lightImpact();
          MixpanelManager().bottomNavigationTabClicked('Chat');
          Navigator.push(
            context,
            MaterialPageRoute(builder: (context) => const ChatPage(isPivotBottom: false)),
          );
        },
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          decoration: BoxDecoration(
            color: const Color(0xFF1F1F25),
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: const Color(0xFF35343B), width: 1),
          ),
          child: Row(
            children: [
              const Icon(FontAwesomeIcons.solidComment, size: 18, color: Colors.deepPurpleAccent),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  context.l10n.askOmiAnything,
                  style: const TextStyle(color: Color(0xFF8E8E93), fontSize: 16),
                ),
              ),
              const Icon(Icons.send_rounded, size: 20, color: Color(0xFF8E8E93)),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildSectionHeader(BuildContext context, String title, {VoidCallback? onViewAll}) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(24, 20, 16, 8),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(title, style: const TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w600)),
          if (onViewAll != null)
            GestureDetector(
              onTap: onViewAll,
              child: Text(
                context.l10n.viewAll,
                style: const TextStyle(color: Colors.deepPurpleAccent, fontSize: 14, fontWeight: FontWeight.w500),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildDailyRecapsPreview(BuildContext context) {
    if (_loadingSummaries) {
      return Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16),
        child: Column(
          children: List.generate(
            2,
            (_) => Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: ShimmerWithTimeout(
                baseColor: AppStyles.backgroundSecondary,
                highlightColor: AppStyles.backgroundTertiary,
                child: Container(
                  height: 72,
                  decoration: BoxDecoration(
                    color: AppStyles.backgroundSecondary,
                    borderRadius: BorderRadius.circular(12),
                  ),
                ),
              ),
            ),
          ),
        ),
      );
    }

    if (_recentSummaries.isEmpty) {
      return Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        child: Container(
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: const Color(0xFF1F1F25),
            borderRadius: BorderRadius.circular(12),
          ),
          child: const Text(
            'No daily recaps yet',
            style: TextStyle(color: Color(0xFF8E8E93), fontSize: 14),
          ),
        ),
      );
    }

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: Column(
        children: _recentSummaries.map((summary) => _buildSummaryCard(context, summary)).toList(),
      ),
    );
  }

  Widget _buildSummaryCard(BuildContext context, DailySummary summary) {
    return GestureDetector(
      onTap: () {
        MixpanelManager().dailySummaryDetailViewed(summaryId: summary.id, date: summary.date);
        Navigator.push(
          context,
          MaterialPageRoute(builder: (context) => DailySummaryDetailPage(summaryId: summary.id)),
        );
      },
      child: Container(
        margin: const EdgeInsets.only(bottom: 10),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        decoration: BoxDecoration(
          color: const Color(0xFF1F1F25),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Row(
          children: [
            if (summary.dayEmoji.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(right: 12),
                child: Text(summary.dayEmoji, style: const TextStyle(fontSize: 24)),
              ),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    summary.headline.isNotEmpty ? summary.headline : summary.overview,
                    style: const TextStyle(color: Colors.white, fontSize: 14, fontWeight: FontWeight.w500),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  const SizedBox(height: 2),
                  Text(
                    _formatSummaryDate(summary.date),
                    style: const TextStyle(color: Color(0xFF8E8E93), fontSize: 12),
                  ),
                ],
              ),
            ),
            const Icon(Icons.chevron_right, color: Color(0xFF8E8E93), size: 18),
          ],
        ),
      ),
    );
  }

  String _formatSummaryDate(String dateStr) {
    if (dateStr.isEmpty) return '';
    try {
      final date = DateTime.parse(dateStr);
      final now = DateTime.now();
      final diff = now.difference(date).inDays;
      if (diff == 0) return 'Today';
      if (diff == 1) return 'Yesterday';
      return '${date.month}/${date.day}';
    } catch (_) {
      return dateStr;
    }
  }

  Widget _buildConversationsPreview(ConversationProvider convoProvider) {
    if (convoProvider.isLoadingConversations && convoProvider.groupedConversations.isEmpty) {
      return SliverToBoxAdapter(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: Column(
            children: List.generate(
              2,
              (_) => Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: ShimmerWithTimeout(
                  baseColor: AppStyles.backgroundSecondary,
                  highlightColor: AppStyles.backgroundTertiary,
                  child: Container(
                    height: 80,
                    decoration: BoxDecoration(
                      color: AppStyles.backgroundSecondary,
                      borderRadius: BorderRadius.circular(12),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      );
    }

    if (convoProvider.groupedConversations.isEmpty) {
      return const SliverToBoxAdapter(child: SizedBox.shrink());
    }

    // Show up to 3 conversation groups
    final keys = convoProvider.groupedConversations.keys.take(3).toList();
    return SliverList(
      delegate: SliverChildBuilderDelegate(
        childCount: keys.length,
        (context, index) {
          final date = keys[index];
          final conversations = convoProvider.groupedConversations[date]!;
          return ConversationsGroupWidget(
            key: ValueKey(date),
            isFirst: index == 0,
            conversations: conversations,
            date: date,
          );
        },
      ),
    );
  }
}
