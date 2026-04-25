import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:font_awesome_flutter/font_awesome_flutter.dart';
import 'package:provider/provider.dart';

import 'package:omi/backend/http/api/users.dart';
import 'package:omi/backend/schema/conversation.dart';
import 'package:omi/backend/schema/daily_summary.dart';
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
            await Future.wait([convoProvider.getInitialConversations(), _loadSummaries()]);
          },
          color: Colors.deepPurpleAccent,
          backgroundColor: Colors.white,
          child: CustomScrollView(
            controller: _scrollController,
            physics: const AlwaysScrollableScrollPhysics(),
            slivers: [
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

              // Bottom padding so content isn't hidden behind chat bar + nav
              const SliverToBoxAdapter(child: SizedBox(height: 160)),
            ],
          ),
        );
      },
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
              padding: const EdgeInsets.only(top: 12),
              child: ShimmerWithTimeout(
                baseColor: AppStyles.backgroundSecondary,
                highlightColor: AppStyles.backgroundTertiary,
                child: Container(
                  height: 80,
                  decoration: BoxDecoration(
                    color: AppStyles.backgroundSecondary,
                    borderRadius: BorderRadius.circular(24),
                  ),
                ),
              ),
            ),
          ),
        ),
      );
    }

    if (_recentSummaries.isEmpty) return const SizedBox.shrink();

    return Column(children: _recentSummaries.map((s) => _buildSummaryCard(context, s)).toList());
  }

  Widget _buildSummaryCard(BuildContext context, DailySummary summary) {
    return GestureDetector(
      onTap: () {
        MixpanelManager().dailySummaryDetailViewed(summaryId: summary.id, date: summary.date);
        Navigator.push(
          context,
          MaterialPageRoute(builder: (context) => DailySummaryDetailPage(summaryId: summary.id, summary: summary)),
        );
      },
      child: Padding(
        padding: const EdgeInsets.only(top: 12, left: 16, right: 16),
        child: Container(
          width: double.maxFinite,
          decoration: BoxDecoration(color: const Color(0xFF1F1F25), borderRadius: BorderRadius.circular(24.0)),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 20),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: 40,
                  height: 40,
                  decoration: BoxDecoration(color: const Color(0xFF35343B), borderRadius: BorderRadius.circular(12)),
                  alignment: Alignment.center,
                  child: Text(summary.dayEmoji, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w500)),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        summary.headline,
                        style: Theme.of(context).textTheme.titleMedium,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                      ),
                      const SizedBox(height: 8),
                      Row(
                        children: [
                          Text(
                            _formatDate(summary.date),
                            style: const TextStyle(color: Color(0xFF9A9BA1), fontSize: 14),
                          ),
                          if (summary.stats.totalConversations > 0) ...[
                            const Text(' • ', style: TextStyle(color: Color(0xFF9A9BA1), fontSize: 14)),
                            const FaIcon(FontAwesomeIcons.solidComments, size: 10, color: Color(0xFF9A9BA1)),
                            const SizedBox(width: 4),
                            Text(
                              '${summary.stats.totalConversations}',
                              style: const TextStyle(color: Color(0xFF9A9BA1), fontSize: 14),
                            ),
                          ],
                          if (summary.stats.actionItemsCount > 0) ...[
                            const Text(' • ', style: TextStyle(color: Color(0xFF9A9BA1), fontSize: 14)),
                            const FaIcon(FontAwesomeIcons.listCheck, size: 11, color: Color(0xFF9A9BA1)),
                            const SizedBox(width: 4),
                            Text(
                              '${summary.stats.actionItemsCount}',
                              style: const TextStyle(color: Color(0xFF9A9BA1), fontSize: 14),
                            ),
                          ],
                        ],
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  String _formatDate(String dateStr) {
    final parts = dateStr.split('-');
    if (parts.length != 3) return dateStr;
    final year = int.tryParse(parts[0]) ?? 2024;
    final month = int.tryParse(parts[1]) ?? 1;
    final day = int.tryParse(parts[2]) ?? 1;
    final date = DateTime(year, month, day);
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final yesterday = today.subtract(const Duration(days: 1));
    if (date == today) return 'Today';
    if (date == yesterday) return 'Yesterday';
    const weekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return '${weekdays[date.weekday - 1]}, ${months[month - 1]} $day';
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
                padding: const EdgeInsets.only(top: 12),
                child: ShimmerWithTimeout(
                  baseColor: AppStyles.backgroundSecondary,
                  highlightColor: AppStyles.backgroundTertiary,
                  child: Container(
                    height: 80,
                    decoration: BoxDecoration(
                      color: AppStyles.backgroundSecondary,
                      borderRadius: BorderRadius.circular(24),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      );
    }

    if (convoProvider.groupedConversations.isEmpty) return const SliverToBoxAdapter(child: SizedBox.shrink());

    final keys = convoProvider.groupedConversations.keys.take(3).toList();
    return SliverList(
      delegate: SliverChildBuilderDelegate(
        childCount: keys.length,
        (context, index) {
          final date = keys[index];
          return ConversationsGroupWidget(
            key: ValueKey(date),
            isFirst: index == 0,
            conversations: convoProvider.groupedConversations[date]!,
            date: date,
          );
        },
      ),
    );
  }
}
