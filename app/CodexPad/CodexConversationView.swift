import SwiftUI

struct CodexConversationView: View {
    @ObservedObject var model: CodexWorkspaceModel

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @State private var followsOutput = true

    var body: some View {
        Group {
            if let thread = model.selectedThread {
                conversation(thread)
            } else if !model.enginePhase.isReady {
                EngineUnavailableView(model: model)
            } else {
                WelcomeWorkspaceView(model: model)
            }
        }
        .background(CodexPalette.canvas)
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func conversation(_ thread: CodexThreadRecord) -> some View {
        VStack(spacing: 0) {
            conversationHeader(thread)
            Divider().overlay(CodexPalette.line)
            if !model.enginePhase.isReady {
                Button("Reconnect local engine") { Task { await model.retryConnection() } }
                    .buttonStyle(.bordered).padding(8)
            }
            timeline
            ComposerBar(model: model)
        }
    }

    private func conversationHeader(_ thread: CodexThreadRecord) -> some View {
        let layout = dynamicTypeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
            : AnyLayout(HStackLayout(spacing: 12))
        return layout {
            VStack(alignment: .leading, spacing: 3) {
                Text(thread.title)
                    .font(.headline)
                    .foregroundStyle(CodexPalette.ink)
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? 2 : 1)
                Label(thread.cwd, systemImage: "folder")
                    .font(.caption.monospaced())
                    .foregroundStyle(CodexPalette.secondaryInk)
                    .lineLimit(1)
            }
            if !dynamicTypeSize.isAccessibilitySize { Spacer() }
            EngineStatusPill(phase: model.enginePhase)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .background(CodexPalette.canvas)
    }

    private var timeline: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 0) {
                    if model.hasEarlierHistory {
                        Button(model.isLoadingHistory ? "Loading history…" : "Load earlier history") {
                            followsOutput = false
                            Task { await model.loadEarlierHistory() }
                        }
                        .buttonStyle(.bordered)
                        .disabled(model.isLoadingHistory)
                        .padding(.bottom, 12)
                    }
                    if let error = model.errorBanner {
                        ErrorBanner(message: error) {
                            model.errorBanner = nil
                        }
                        .padding(.bottom, 12)
                    }

                    let items = model.selectedTimeline
                    ForEach(items.indices, id: \.self) { index in
                        let item = items[index]
                        TimelineCard(
                            item: item,
                            isFirst: index == 0,
                            isLast: index == items.count - 1 && relevantRequests.isEmpty
                        )
                        .id(item.id)
                    }

                    ForEach(relevantRequests) { request in
                        CodexServerRequestView(model: model, request: request)
                        .padding(.leading, 0)
                    }

                    if model.isTurnRunning {
                        WorkingIndicator()
                            .id("working")
                    }
                    Color.clear.frame(height: 1).id("timeline-end")
                        .onAppear { followsOutput = true }
                        .onDisappear { followsOutput = false }
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 18)
                .frame(maxWidth: 840)
                .frame(maxWidth: .infinity)
            }
            .scrollDismissesKeyboard(model.desktopModeEnabled ? .never : .interactively)
            .onChange(of: model.selectedTimeline) { _, _ in
                if followsOutput { proxy.scrollTo("timeline-end", anchor: .bottom) }
            }
            .onChange(of: model.pendingRequests.count) { _, _ in
                scrollToEnd(proxy)
            }
            .onChange(of: model.isTurnRunning) { _, _ in
                if followsOutput { scrollToEnd(proxy) }
            }
            .onChange(of: model.selectedThreadID) { _, _ in
                followsOutput = true
                scrollToEnd(proxy)
            }
            .overlay(alignment: .bottomTrailing) {
                if !followsOutput {
                    Button("Latest", systemImage: "arrow.down") {
                        followsOutput = true
                        scrollToEnd(proxy)
                    }
                    .buttonStyle(.borderedProminent)
                    .padding()
                }
            }
        }
    }

    private var relevantRequests: [PendingServerRequest] {
        model.pendingRequests.filter {
            $0.threadID == nil || $0.threadID == model.selectedThreadID
        }
    }

    private func scrollToEnd(_ proxy: ScrollViewProxy) {
        if reduceMotion {
            proxy.scrollTo("timeline-end", anchor: .bottom)
        } else {
            withAnimation(.easeOut(duration: 0.24)) {
                proxy.scrollTo("timeline-end", anchor: .bottom)
            }
        }
    }
}

private struct ComposerBar: View {
    @ObservedObject var model: CodexWorkspaceModel
    @FocusState private var isFocused: Bool
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        VStack(spacing: 12) {
            TextField("Ask for follow-up changes", text: $model.composerText, axis: .vertical)
                .font(.body)
                .lineLimit(1...(dynamicTypeSize.isAccessibilitySize ? 3 : 7))
                .frame(minWidth: 80, maxWidth: .infinity, minHeight: 40, alignment: .topLeading)
                .focused($isFocused)
                .accessibilityLabel("Message Codex")
                .accessibilityIdentifier("codexpad.composer")
                .onChange(of: isFocused) { _, focused in
                    if focused { model.composerDidGainFocus() }
                }
                .onChange(of: model.composerFocusGeneration) { _, _ in
                    guard model.desktopModeEnabled else { return }
                    isFocused = true
                }
                .onChange(of: model.desktopModeEnabled) { _, enabled in
                    if !enabled { isFocused = false }
                }
            HStack(alignment: .center, spacing: 6) {
                Menu {
                    Button("Settings", systemImage: "gearshape") { model.showsSettings = true }
                    Button("Features", systemImage: "square.grid.2x2") { model.showsFeatureCenter = true }
                } label: {
                    Image(systemName: "plus").frame(width: 44, height: 44)
                }
                .accessibilityLabel("Conversation options")
                modelControls
                if model.isTurnRunning {
                    Button {
                        if !model.desktopModeEnabled { isFocused = false }
                        Task { await model.interruptTurn() }
                    } label: {
                        Image(systemName: "stop.fill")
                            .frame(width: 44, height: 44)
                    }
                    .buttonStyle(.borderedProminent).buttonBorderShape(.circle)
                    .tint(CodexPalette.cobalt)
                    .keyboardShortcut(".", modifiers: .command)
                    .accessibilityLabel("Stop the current turn")
                } else {
                    Button {
                        if !model.desktopModeEnabled { isFocused = false }
                        Task { await model.sendComposer() }
                    } label: {
                        Image(systemName: "arrow.up").font(.body.weight(.semibold))
                            .frame(width: 44, height: 44)
                    }
                    .buttonStyle(.borderedProminent).buttonBorderShape(.circle)
                    .tint(CodexPalette.cobalt)
                    .disabled(!model.enginePhase.isReady || model.isCreatingThread || model.composerText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    .keyboardShortcut(.return, modifiers: .command)
                    .accessibilityLabel("Send message")
                    .accessibilityIdentifier("codexpad.send")
                }
            }
            .foregroundStyle(CodexPalette.secondaryInk)
        }
        .padding(.horizontal, 16).padding(.top, 16).padding(.bottom, 8)
        .background(CodexPalette.raised, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 24, style: .continuous)
                .stroke(CodexPalette.line, lineWidth: 1)
        }
        .shadow(color: .black.opacity(0.04), radius: 8, y: 3)
        .padding(.horizontal, 20).padding(.vertical, 16)
        .frame(maxWidth: 840)
        .frame(maxWidth: .infinity)
        .background(CodexPalette.canvas)
    }

    private var modelControls: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                Menu {
                    ForEach(model.availableModels.filter { !$0.hidden }) { option in
                        Button {
                            model.selectModel(option.id)
                        } label: {
                            if option.id == model.selectedModelID {
                                Label(option.displayName, systemImage: "checkmark")
                            } else {
                                Text(option.displayName)
                            }
                        }
                    }
                    if model.availableModels.contains(where: \.hidden) {
                        Section("Hidden provider entries") {
                            ForEach(model.availableModels.filter(\.hidden)) { option in
                                Button("\(option.displayName) - Hidden") {
                                    model.selectModel(option.id)
                                }
                            }
                        }
                    }
                } label: {
                    Label(model.selectedModel?.displayName ?? "Model", systemImage: "chevron.down")
                }
                .buttonStyle(.plain)
                    .frame(minHeight: 44)
                .disabled(model.availableModels.isEmpty)
                .accessibilityIdentifier("codexpad.model-picker")

                if let selected = model.selectedModel, !selected.reasoningEfforts.isEmpty {
                    Menu {
                        ForEach(selected.reasoningEfforts) { option in
                            Button {
                                model.selectedReasoningEffort = option.effort
                                model.selectedCollaborationMode = nil
                            } label: {
                                if option.effort == model.selectedReasoningEffort {
                                    Label(option.effort.capitalized, systemImage: "checkmark")
                                } else {
                                    Text(option.effort.capitalized)
                                }
                            }
                        }
                    } label: {
                        Label(model.selectedReasoningEffort?.capitalized ?? "Reasoning", systemImage: "chevron.down")
                    }
                    .buttonStyle(.plain)
                    .frame(minHeight: 44)
                    .accessibilityIdentifier("codexpad.reasoning-picker")

                    if model.showsCompleteFeatureSet, !selected.serviceTiers.isEmpty {
                        Menu {
                            Button("Provider default") { model.selectedServiceTier = nil }
                            ForEach(selected.serviceTiers) { tier in
                                Button(tier.name) { model.selectedServiceTier = tier.id }
                            }
                        } label: {
                            let tierName = selected.serviceTiers.first { $0.id == model.selectedServiceTier }?.name
                            Label(tierName ?? "Service tier", systemImage: "speedometer")
                        }
                        .buttonStyle(.plain)
                    .frame(minHeight: 44)
                    }
                }

                if model.showsCompleteFeatureSet, !model.collaborationModes.isEmpty {
                    Menu {
                        Button("Standard") { model.selectedCollaborationMode = nil }
                        ForEach(model.collaborationModes) { mode in
                            Button(mode.name) { model.selectedCollaborationMode = mode.name }
                        }
                    } label: {
                        Label(model.selectedCollaborationMode ?? "Collaboration", systemImage: "person.2")
                    }
                    .buttonStyle(.plain)
                    .frame(minHeight: 44)
                    .accessibilityIdentifier("codexpad.collaboration-picker")
                }
            }
        }
        .font(.subheadline)
        .controlSize(.small)
        .frame(minHeight: 44)
    }
}

private struct WorkingIndicator: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "sparkles")
                .foregroundStyle(CodexPalette.cobalt)
                .symbolEffect(.pulse, isActive: !reduceMotion)
            Text("Working…")
                .font(.subheadline.weight(.medium))
                .foregroundStyle(CodexPalette.secondaryInk)
            Spacer()
        }
        .padding(.leading, 0)
        .padding(.vertical, 16)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Working…")
    }
}

private struct ErrorBanner: View {
    let message: String
    let dismiss: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(CodexPalette.danger)
            Text(message)
                .font(.callout)
                .foregroundStyle(CodexPalette.ink)
                .frame(maxWidth: .infinity, alignment: .leading)
            Button(action: dismiss) {
                Image(systemName: "xmark")
                    .frame(width: 32, height: 32)
            }
            .accessibilityLabel("Dismiss error")
        }
        .codexPanel(padding: 12)
    }
}

private struct WelcomeWorkspaceView: View {
    @ObservedObject var model: CodexWorkspaceModel

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                Spacer(minLength: 100)
                Text("What will you build?")
                    .font(.system(.largeTitle, design: .default, weight: .semibold))
                    .foregroundStyle(CodexPalette.ink)
                    .multilineTextAlignment(.center)
                Button {
                    model.showsSettings = true
                } label: {
                    Label(model.workspacePath.split(separator: "/").last.map(String.init) ?? model.workspacePath,
                          systemImage: "folder")
                        .font(.subheadline)
                        .padding(.horizontal, 12).frame(minHeight: 44)
                }
                .buttonStyle(.plain)
                .foregroundStyle(CodexPalette.secondaryInk)
                .accessibilityLabel("Workspace settings, \(model.workspacePath)")
                ComposerBar(model: model)
                if let error = model.errorBanner {
                    ErrorBanner(message: error) { model.errorBanner = nil }
                        .padding(.horizontal, 20)
                }
                Text("Start a conversation in your workspace.")
                    .font(.footnote)
                    .foregroundStyle(CodexPalette.secondaryInk)
            }
            .frame(maxWidth: 840)
            .frame(maxWidth: .infinity)
            .padding(.bottom, 24)
        }
        .scrollDismissesKeyboard(model.desktopModeEnabled ? .never : .interactively)
    }
}

private struct EngineUnavailableView: View {
    @ObservedObject var model: CodexWorkspaceModel

    var body: some View {
        ContentUnavailableView {
            Label(model.enginePhase.title, systemImage: "shippingbox.and.arrow.backward")
        } description: {
            switch model.enginePhase {
            case .offline(let message): Text(message)
            default: Text("Starting Codex in your workspace.")
            }
        } actions: {
            if case .offline = model.enginePhase {
                Button("Try again") {
                    Task { await model.retryConnection() }
                }
                .buttonStyle(.borderedProminent)
            } else {
                ProgressView()
                    .controlSize(.large)
                    .accessibilityLabel("Starting local Codex engine")
            }
        }
    }
}
