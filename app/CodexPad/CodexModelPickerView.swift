import SwiftUI

struct CodexModelPickerButton: View {
    @ObservedObject var model: CodexWorkspaceModel
    var title: String? = nil
    @State private var isPresented = false

    var body: some View {
        Button {
            isPresented = true
        } label: {
            Label(title ?? model.selectedModel?.displayName ?? "Model", systemImage: "chevron.down")
        }
        .accessibilityIdentifier("codexpad.model-picker")
        .sheet(isPresented: $isPresented) {
            CodexModelPickerView(model: model)
        }
    }
}

struct CodexModelPickerView: View {
    @ObservedObject var model: CodexWorkspaceModel
    @Environment(\.dismiss) private var dismiss
    @State private var searchText = ""
    @State private var customModelID = ""
    @State private var customModelError: String?

    private var matchingModels: [CodexModelOption] {
        model.availableModels.filter {
            searchText.isEmpty || $0.displayName.localizedCaseInsensitiveContains(searchText)
                || $0.model.localizedCaseInsensitiveContains(searchText)
        }
    }

    var body: some View {
        NavigationStack {
            List {
                modelSection("Provider models", options: matchingModels.filter { !$0.hidden && !$0.isCustom })
                modelSection("Hidden provider models", options: matchingModels.filter { $0.hidden && !$0.isCustom })
                let customModels = matchingModels.filter(\.isCustom)
                if !customModels.isEmpty {
                    Section("Custom models") {
                        ForEach(customModels) { option in modelRow(option) }
                            .onDelete { offsets in
                                let slugs = offsets.map { customModels[$0].model }
                                for slug in slugs { model.removeCustomModel(slug) }
                            }
                    }
                }
                if matchingModels.isEmpty {
                    Text(model.isRefreshingModels ? "Loading models…" : "No matching models")
                        .foregroundStyle(CodexPalette.secondaryInk)
                }
                Section {
                    TextField("Exact model ID", text: $customModelID)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .submitLabel(.done)
                        .onSubmit(addCustomModel)
                        .onChange(of: customModelID) { _, _ in customModelError = nil }
                        .accessibilityIdentifier("codexpad.custom-model-id")
                    Button("Add and select model", action: addCustomModel)
                        .disabled(customModelID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    if let customModelError {
                        Text(customModelError).foregroundStyle(CodexPalette.danger)
                    }
                } header: {
                    Text("Add a model by ID")
                } footer: {
                    Text("Use an exact model ID supplied by your provider. Your account must have access. Custom models use provider defaults for reasoning and service tier.")
                }
            }
            .searchable(text: $searchText, prompt: "Search model names or IDs")
            .navigationTitle("Models")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Refresh", systemImage: "arrow.clockwise") {
                        Task { await model.refreshModels() }
                    }
                    .disabled(!model.enginePhase.isReady || model.isRefreshingModels)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .task { await model.refreshModels() }
        }
    }

    @ViewBuilder
    private func modelSection(_ title: String, options: [CodexModelOption]) -> some View {
        if !options.isEmpty {
            Section(title) {
                ForEach(options) { option in modelRow(option) }
            }
        }
    }

    private func modelRow(_ option: CodexModelOption) -> some View {
        Button {
            model.selectModel(option.id)
            dismiss()
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text(option.displayName).foregroundStyle(CodexPalette.ink)
                    Text(option.model).font(.caption.monospaced())
                        .foregroundStyle(CodexPalette.secondaryInk)
                }
                Spacer()
                if option.id == model.selectedModelID {
                    Image(systemName: "checkmark").foregroundStyle(CodexPalette.cobalt)
                }
            }
        }
    }

    private func addCustomModel() {
        if model.addCustomModel(customModelID) {
            dismiss()
        } else {
            customModelError = "Enter a model ID of up to 200 characters using letters, numbers, hyphens, underscores, periods, colons or slashes."
        }
    }
}
