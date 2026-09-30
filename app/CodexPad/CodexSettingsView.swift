import SwiftUI

struct CodexSettingsView: View {
    @ObservedObject var model: CodexWorkspaceModel
    @Environment(\.dismiss) private var dismiss

    @State private var apiKey = ""
    @State private var confirmsUnlink = false

    var body: some View {
        NavigationStack {
            Form {
                if let error = model.errorBanner {
                    Section("Action failed") {
                        Text(error).foregroundStyle(CodexPalette.danger).textSelection(.enabled)
                        Button("Dismiss error") { model.errorBanner = nil }
                    }
                }
                inputSection
                modelSection
                accountSection
                workspaceSection
                featureSection

                Section("Safety") {
                    Label("Commands and file writes require native approval when Codex requests it.", systemImage: "hand.raised")
                    Label("The app-server is bound only to 127.0.0.1 inside the app.", systemImage: "lock.shield")
                }

                Section("Platform behavior") {
                    Text("iPadOS may suspend active work when CodexPad is backgrounded. Keep the app visible for long turns and builds.")
                    Text("The terminal remains available as a recovery surface from the workspace toolbar.")
                }

                Section("Open source") {
                    LabeledContent("Codex", value: "Apache-2.0")
                    LabeledContent("iSH", value: "GPL with iOS permission")
                    Text("Source and third-party notices are included with every release.")
                }
            }
            .accessibilityIdentifier("codexpad.settings-form")
            .safeAreaInset(edge: .bottom, spacing: 0) {
                Color.clear
                    .frame(height: 24)
                    .accessibilityHidden(true)
            }
            .navigationTitle("Settings")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .toolbarBackground(.ultraThinMaterial, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .confirmationDialog(
                "Unlink the Files folder?",
                isPresented: $confirmsUnlink,
                titleVisibility: .visible
            ) {
                Button("Unlink", role: .destructive) {
                    Task { await model.unlinkFilesFolder() }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("The folder and its files are not deleted. CodexPad only removes its saved access and iSH mount.")
            }
        }
    }

    private var inputSection: some View {
        Section {
            Toggle("Desktop mode", isOn: $model.desktopModeEnabled)
                .accessibilityIdentifier("codexpad.desktop-mode")

            if !model.desktopModeEnabled {
                Toggle("Show all Codex features", isOn: $model.showAllFeaturesInTouchMode)
                    .accessibilityIdentifier("codexpad.touch-show-all")
            } else {
                Label("All compatible features are always visible in desktop mode.", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(CodexPalette.teal)
            }
        } header: {
            Text("Input mode")
        } footer: {
            Text(inputModeDescription)
        }
    }

    private var inputModeDescription: String {
        if model.desktopModeEnabled {
            return "Desktop mode is optimized for a pointer and hardware keyboard. After the composer is engaged once, focus returns after sends, stops, navigation, settings, and terminal use."
        }
        if model.showAllFeaturesInTouchMode {
            return "Touch input and software-keyboard behavior stay active while the complete Feature Center and long-tail controls are visible."
        }
        return "Touch mode prioritizes direct input and software-keyboard behavior while hiding less-used controls."
    }

    private var modelSection: some View {
        Section {
            if model.availableModels.isEmpty {
                LabeledContent("Catalog") {
                    ProgressView().controlSize(.small)
                }
            } else {
                Picker("Model", selection: modelSelection) {
                    ForEach(model.availableModels.filter { !$0.hidden }) { option in
                        Text(option.displayName).tag(option.id)
                    }
                    if model.availableModels.contains(where: \.hidden) {
                        Section("Hidden provider entries") {
                            ForEach(model.availableModels.filter(\.hidden)) { option in
                                Text("\(option.displayName) - Hidden").tag(option.id)
                            }
                        }
                    }
                }

                if let selected = model.selectedModel {
                    Picker("Reasoning", selection: reasoningSelection) {
                        ForEach(selected.reasoningEfforts) { effort in
                            Text(effort.effort.capitalized).tag(effort.effort)
                        }
                    }

                    if model.showsCompleteFeatureSet, !selected.serviceTiers.isEmpty {
                        Picker("Service tier", selection: serviceTierSelection) {
                            Text("Provider default").tag("")
                            ForEach(selected.serviceTiers) { tier in
                                Text(tier.name).tag(tier.id)
                            }
                        }
                    }
                }

                if model.showsCompleteFeatureSet, !model.collaborationModes.isEmpty {
                    Picker("Collaboration", selection: collaborationSelection) {
                        Text("Standard").tag("")
                        ForEach(model.collaborationModes) { mode in
                            Text(mode.name).tag(mode.name)
                        }
                    }
                }
            }

            Button("Refresh model catalog") {
                Task {
                    await model.refreshModels()
                    await model.refreshCollaborationModes()
                }
            }
            .disabled(!model.enginePhase.isReady)
        } header: {
            Text("Model")
        } footer: {
            if let selected = model.selectedModel, !selected.description.isEmpty {
                Text(selected.description)
            } else {
                Text("The catalog is loaded from the active Codex provider and includes its supported reasoning and collaboration options.")
            }
        }
    }

    private var accountSection: some View {
        Section("Account") {
            if model.account.isAuthenticated {
                LabeledContent("Signed in", value: model.account.displayName)
                if let plan = model.account.plan {
                    LabeledContent("Plan", value: plan.capitalized)
                }
                Button("Sign out", role: .destructive) {
                    Task { await model.signOut() }
                }
            } else {
                Button("Continue with ChatGPT") {
                    Task { await model.signInWithChatGPT() }
                }
                .disabled(!model.enginePhase.isReady || model.isSigningIn)
                Button("Use a device code") {
                    Task { await model.signInWithDeviceCode() }
                }
                .disabled(!model.enginePhase.isReady || model.isSigningIn)
                SecureField("OpenAI API key", text: $apiKey)
                    .textContentType(.password)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                Button("Save API key") {
                    let submittedKey = apiKey
                    Task {
                        if await model.signIn(apiKey: submittedKey), apiKey == submittedKey {
                            apiKey = ""
                        }
                    }
                }
                .disabled(apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                          || !model.enginePhase.isReady || model.isSigningIn)
            }

            if let code = model.deviceCode, let url = model.deviceVerificationURL {
                LabeledContent("Device code") { Text(code).font(.body.monospaced()).textSelection(.enabled) }
                Button("Open \(url.host ?? "verification page")") {
                    model.reopenSignInBrowser()
                }
                Text("If device login is unavailable, enable it in your ChatGPT security settings or workspace permissions.")
                    .font(.footnote).foregroundStyle(CodexPalette.secondaryInk)
            }
            if model.isSigningIn {
                HStack {
                    ProgressView()
                    Text(model.isStartingSignIn ? "Starting sign-in…" : "Waiting for sign-in…")
                }
                if model.pendingLoginID != nil && model.deviceCode == nil {
                    Button("Reopen sign-in") { model.reopenSignInBrowser() }
                }
                Button("Cancel sign-in", role: .cancel) {
                    Task { await model.cancelSignIn() }
                }
            }
            if !model.enginePhase.isReady {
                Button("Reconnect Codex engine") { Task { await model.retryConnection() } }
            }
        }
    }

    private var workspaceSection: some View {
        Section {
            switch model.linkedFolderPhase {
            case .disconnected:
                Button("Choose folder in Files", systemImage: "folder.badge.plus") {
                    Task { await model.chooseFilesFolder() }
                }
                .disabled(!model.enginePhase.isReady)
            case .choosing:
                HStack {
                    ProgressView()
                    Text("Waiting for Files selection...")
                }
            case .linked(let name):
                LabeledContent("Linked folder") {
                    Text(name)
                        .accessibilityIdentifier("codexpad.linked-folder-name")
                }
                Button("Unlink Files folder", role: .destructive) {
                    confirmsUnlink = true
                }
            case .needsRelink(let message):
                Label(message, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(CodexPalette.amber)
                Button("Select folder again") {
                    Task { await model.chooseFilesFolder() }
                }
            }

            TextField("Guest path", text: $model.workspacePath)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .font(.body.monospaced())
            LabeledContent("Runtime", value: "iSH – Alpine ARM64")
            LabeledContent("Transport", value: "Guest loopback")
        } header: {
            Text("Workspace")
        } footer: {
            Text(workspaceDescription)
        }
    }

    private var workspaceDescription: String {
        switch model.linkedFolderPhase {
        case .disconnected:
            "Choose a folder to mount it directly into iSH and make it Codex’s working directory. The guest path above is the default local workspace."
        case .choosing:
            "CodexPad is waiting for a security-scoped folder selection from Files."
        case .linked:
            "This Files folder is mounted as a distinct linked workspace; unlinking removes access without deleting any files."
        case .needsRelink:
            "The saved Files permission is no longer usable. Select the folder again to restore its iSH mount."
        }
    }

    private var featureSection: some View {
        Section {
            LabeledContent("Compatible operations", value: "\(CodexFeatureCatalog.compatibleFeatureCount)")
            LabeledContent("Platform exceptions", value: "\(CodexFeatureCatalog.unavailableFeatureCount)")
            if model.showsCompleteFeatureSet {
                Button("Open complete Feature Center", systemImage: "square.grid.3x3") {
                    model.opensFeaturesAfterSettings = true
                    dismiss()
                }
                .accessibilityIdentifier("codexpad.open-feature-center")
            }
        } header: {
            Text("Codex feature coverage")
        } footer: {
            if !model.showsCompleteFeatureSet {
                Text("Turn on Show all Codex features above to reveal advanced, experimental, and long-tail operations while staying in touch mode.")
            } else {
                Text("Every compatible operation in the pinned app-server protocol is available through a native control or the structured Feature Center.")
            }
        }
    }

    private var modelSelection: Binding<String> {
        Binding(
            get: { model.selectedModelID ?? model.availableModels.first?.id ?? "" },
            set: { model.selectModel($0) }
        )
    }

    private var reasoningSelection: Binding<String> {
        Binding(
            get: { model.selectedReasoningEffort ?? model.selectedModel?.defaultReasoningEffort ?? "" },
            set: { model.selectedReasoningEffort = $0 }
        )
    }

    private var serviceTierSelection: Binding<String> {
        Binding(
            get: { model.selectedServiceTier ?? "" },
            set: { model.selectedServiceTier = $0.isEmpty ? nil : $0 }
        )
    }

    private var collaborationSelection: Binding<String> {
        Binding(
            get: { model.selectedCollaborationMode ?? "" },
            set: { model.selectedCollaborationMode = $0.isEmpty ? nil : $0 }
        )
    }
}
