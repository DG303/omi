import Combine
import Foundation
import SwiftUI

/// A running or finished background "agent" launched from the Ask Omi floating
/// bar. Each pill owns its own `ChatProvider` so multiple agents can execute in
/// parallel without sharing message state.
@MainActor
final class AgentPill: ObservableObject, Identifiable {
    enum Status: Equatable {
        case starting
        case running
        case done
        case failed(String)

        var displayLabel: String {
            switch self {
            case .starting, .running: return "Running"
            case .done: return "Done"
            case .failed: return "Failed"
            }
        }
    }

    let id = UUID()
    let query: String
    let title: String
    let createdAt: Date
    let model: String

    @Published var status: Status = .starting
    @Published var latestActivity: String = "Starting…"
    @Published var transcript: [String] = []
    @Published var aiMessage: ChatMessage?
    @Published var completedAt: Date?
    @Published var suggestedFollowUps: [String] = []

    /// Convenience: how long the agent has been running (or ran).
    var elapsed: TimeInterval {
        (completedAt ?? Date()).timeIntervalSince(createdAt)
    }

    init(query: String, model: String) {
        self.query = query
        self.model = model
        self.title = AgentPill.deriveTitle(from: query)
        self.createdAt = Date()
    }

    /// Pull a short uppercase title out of the query for the pill popover header.
    /// "open google.com and find vegan ramen" → "OPEN GOOGLE.COM"
    private static func deriveTitle(from query: String) -> String {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        let words = trimmed
            .split(separator: " ", maxSplits: 4, omittingEmptySubsequences: true)
            .prefix(3)
            .map(String.init)
        let joined = words.joined(separator: " ").uppercased()
        if joined.count > 32 {
            return String(joined.prefix(32)) + "…"
        }
        return joined.isEmpty ? "AGENT" : joined
    }
}

/// Singleton that owns the running `AgentPill`s. Spawning a pill creates a new
/// `ChatProvider` and observes its message stream until the agent finishes.
@MainActor
final class AgentPillsManager: ObservableObject {
    static let shared = AgentPillsManager()

    @Published private(set) var pills: [AgentPill] = []
    @Published var hoveredPillID: UUID?
    @Published var pinnedPillID: UUID?

    /// Configurable soft cap so the row never grows past a reasonable width.
    private let maxPills: Int = 8

    private var providers: [UUID: ChatProvider] = [:]
    private var cancellables: [UUID: AnyCancellable] = [:]

    private init() {}

    /// Whether a piece of user input looks like an "action" the agent should
    /// execute, vs. a quick conversational question.
    static func looksLikeAction(_ text: String) -> Bool {
        let lower = text
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
        guard !lower.isEmpty else { return false }
        let actionPrefixes = [
            "open ", "do ", "go ", "send ", "make ", "find ", "search ",
            "build ", "fix ", "create ", "click ", "buy ", "book ",
            "schedule ", "draft ", "write ", "reply ", "compose ", "start ",
            "deploy ", "merge ", "push ", "pull ", "checkout ", "commit ",
            "close ", "delete ", "remove ", "add ", "install ", "update ",
            "edit ", "rename ", "move ", "copy ", "show me ", "help me ",
            "can you ", "please ", "summarize ", "summarise ", "research ",
            "look up ", "look at ", "fill ", "submit ", "post ", "tweet ",
            "dm ", "email ", "call ", "navigate ", "visit ",
        ]
        if actionPrefixes.contains(where: { lower.hasPrefix($0) }) { return true }
        // Long, detailed instructions are almost always actions.
        if lower.count >= 70 { return true }
        return false
    }

    /// Spawn a new agent pill for the given user query.
    @discardableResult
    func spawn(query: String, model: String) -> AgentPill {
        let pill = AgentPill(query: query, model: model)

        // Trim if we're at the cap — drop the oldest non-running pill first,
        // otherwise drop the oldest pill regardless of status.
        if pills.count >= maxPills {
            if let idx = pills.firstIndex(where: { $0.status != .running && $0.status != .starting }) {
                cleanup(pillID: pills[idx].id)
            } else {
                cleanup(pillID: pills[0].id)
            }
        }

        pills.append(pill)

        let provider = ChatProvider()
        // Inherit the working directory from the floating bar's provider so
        // shell-based agents land in the user's expected cwd.
        if let floatingProvider = FloatingControlBarManager.shared.sharedFloatingProvider {
            provider.workingDirectory = floatingProvider.workingDirectory
            provider.modelOverride = floatingProvider.modelOverride
        }
        providers[pill.id] = provider

        let messageCountBefore = provider.messages.count
        cancellables[pill.id] = provider.$messages
            .receive(on: DispatchQueue.main)
            .sink { [weak self, weak pill] messages in
                guard let self, let pill else { return }
                self.handle(messages: messages, since: messageCountBefore, for: pill)
            }

        Task { @MainActor [weak self] in
            await provider.sendMessage(
                query,
                model: model,
                systemPromptPrefix: ChatProvider.floatingBarSystemPromptPrefix,
                sessionKey: "agent-\(pill.id.uuidString)"
            )
            self?.complete(pill: pill, provider: provider)
        }

        return pill
    }

    /// Force-dismiss a pill and free its provider.
    func dismiss(pillID: UUID) {
        cleanup(pillID: pillID)
        if hoveredPillID == pillID { hoveredPillID = nil }
        if pinnedPillID == pillID { pinnedPillID = nil }
    }

    /// Remove all completed (done or failed) pills.
    func clearCompleted() {
        let toRemove = pills.filter {
            switch $0.status {
            case .done, .failed: return true
            default: return false
            }
        }
        for pill in toRemove {
            cleanup(pillID: pill.id)
        }
    }

    private func cleanup(pillID: UUID) {
        cancellables[pillID]?.cancel()
        cancellables[pillID] = nil
        providers[pillID] = nil
        pills.removeAll { $0.id == pillID }
    }

    private func handle(messages: [ChatMessage], since: Int, for pill: AgentPill) {
        guard messages.count > since else { return }
        let recent = Array(messages.suffix(from: since))
        guard let aiMessage = recent.last(where: { $0.sender == .ai }) else { return }
        pill.aiMessage = aiMessage

        if pill.status == .starting {
            pill.status = .running
        }

        let activity = describeActivity(for: aiMessage)
        if !activity.isEmpty && activity != pill.latestActivity {
            pill.latestActivity = activity
            pill.transcript.append(activity)
        }
    }

    private func describeActivity(for message: ChatMessage) -> String {
        for block in message.contentBlocks.reversed() {
            switch block {
            case .toolCall(_, let name, _, _, let input, _):
                let display = ChatContentBlock.displayName(for: name)
                if let input, !input.summary.isEmpty {
                    return "\(display) — \(input.summary)"
                }
                return display
            case .text(_, let text):
                let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
                if !trimmed.isEmpty {
                    return String(trimmed.prefix(110))
                }
            case .thinking, .discoveryCard:
                continue
            }
        }
        let trimmedFallback = message.text.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmedFallback.isEmpty {
            return String(trimmedFallback.prefix(110))
        }
        return "Working…"
    }

    private func complete(pill: AgentPill, provider: ChatProvider) {
        if let errorText = provider.errorMessage, !errorText.isEmpty {
            pill.status = .failed(errorText)
            pill.latestActivity = errorText
        } else {
            pill.status = .done
            if let last = pill.aiMessage, !last.text.isEmpty {
                let trimmed = last.text.trimmingCharacters(in: .whitespacesAndNewlines)
                pill.latestActivity = String(trimmed.prefix(140))
            } else {
                pill.latestActivity = "Done"
            }
        }
        pill.completedAt = Date()
        pill.suggestedFollowUps = AgentPillsManager.deriveFollowUps(for: pill)
    }

    /// Tiny heuristic to suggest 1–2 follow-ups based on the original query.
    /// Real implementation would ask the model — kept simple for the demo.
    private static func deriveFollowUps(for pill: AgentPill) -> [String] {
        let lower = pill.query.lowercased()
        if lower.contains("email") || lower.contains("reply") {
            return ["Open thread", "Check for replies"]
        }
        if lower.contains("search") || lower.contains("find") || lower.contains("look") {
            return ["Open results", "Refine search"]
        }
        if lower.contains("schedule") || lower.contains("book") || lower.contains("calendar") {
            return ["Open calendar", "Add reminder"]
        }
        return ["Open chat", "Run again"]
    }
}
