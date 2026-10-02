import Foundation

@MainActor
final class FakeRPC: CodexRPCServing {
    var inboundHandler: ((RPCInbound) -> Void)?
    var stateHandler: ((CodexRPCClient.State) -> Void)?
    var calls: [(String, JSONValue?)] = []
    var responses: [JSONValue] = []
    var handler: ((String, JSONValue?) async throws -> JSONValue)?
    var connectHandler: (() async throws -> Void)?
    var connectionAttempts = 0
    func connect() async throws {
        connectionAttempts += 1
        try await connectHandler?()
        try Task.checkCancellation()
        stateHandler?(.connected)
    }
    func disconnect() { stateHandler?(.disconnected) }
    func request(method: String, params: JSONValue?) async throws -> JSONValue {
        calls.append((method, params))
        return try await handler?(method, params) ?? .object([:])
    }
    func respond(to id: JSONValue, result: JSONValue) async throws { responses.append(result) }
    func respondUnsupported(to id: JSONValue, method: String) async throws {}
    func event(_ method: String, _ params: JSONValue) { inboundHandler?(.notification(method: method, params: params)) }
}

@MainActor
final class Gate {
    var continuation: CheckedContinuation<JSONValue, Never>?
    func response() async -> JSONValue { await withCheckedContinuation { continuation = $0 } }
    func release(_ value: JSONValue) { continuation?.resume(returning: value); continuation = nil }
}

@main
@MainActor
struct ModelRegression {
    static var checks = 0
    static func check(_ condition: @autoclosure () -> Bool, _ message: String) {
        precondition(condition(), message)
        checks += 1
        print("PASS: \(message)")
    }
    static func until(_ ready: () -> Bool) async {
        let deadline = Date().addingTimeInterval(5)
        while !ready() {
            precondition(Date() < deadline, "Timed out awaiting test gate")
            await Task.yield()
        }
    }
    static func model(_ rpc: FakeRPC) -> CodexWorkspaceModel {
        let preferences = UserDefaults(suiteName: "CodexPad.Regression.\(UUID().uuidString)")!
        let model = CodexWorkspaceModel(rpc: rpc, demoMode: false, preferences: preferences)
        model.enginePhase = .ready
        model.threads = ["A", "B"].map {
            CodexThreadRecord(id: $0, title: $0, preview: "", cwd: "/root/\($0)", updatedAt: .now, activity: .idle)
        }
        model.selectedThreadID = "A"
        return model
    }
    static func turn(_ thread: String, _ id: String, status: String = "inProgress") -> JSONValue {
        .object(["threadId": .string(thread), "turn": .object(["id": .string(id), "status": .string(status), "items": .array([])])])
    }
    static func main() async {
        check(CodexFeatureCatalog.parameterSchema(for: "turn/start") != nil, "pinned request schema is loadable")
        check(CodexFeatureCatalog.upstreamRevision?.count == 40 && CodexFeatureCatalog.upstreamProtocolURL?.path.contains(CodexFeatureCatalog.upstreamRevision!) == true, "protocol source links resolve from the bundled upstream manifest")
        check(CodexFeatureCatalog.missingRequiredParameters(method: "turn/start", params: .object([:])).contains("threadId"), "schema requires a thread ID before advanced execution")
        check(CodexFeatureCatalog.parameterReference(for: "turn/start")?.contains("definitions") == true, "nested parameter definitions are available offline")
        let versionRPC = FakeRPC()
        let versionModel = model(versionRPC)
        var guestRevision = CodexFeatureCatalog.upstreamRevision!
        versionRPC.handler = { method, _ in
            if method == "fs/readFile" {
                let data = try JSONEncoder().encode(JSONValue.object(["codexRevision": .string(guestRevision)]))
                return .object(["dataBase64": .string(data.base64EncodedString())])
            }
            return .object(["data": .array([])])
        }
        await versionModel.connectToLocalEngine()
        check(versionModel.enginePhase.isReady && versionModel.runtimeRevision == guestRevision, "matching saved runtime is verified before workspace refresh")
        guestRevision = String(repeating: "0", count: 40)
        versionRPC.calls.removeAll()
        await versionModel.connectToLocalEngine()
        check(!versionModel.enginePhase.isReady && versionModel.errorBanner?.contains("have not been changed") == true, "older saved runtime blocks incompatible GUI operations without modifying user data")
        check(versionRPC.calls.map(\.0) == ["fs/readFile"], "runtime mismatch issues only a read-only manifest check")
        versionRPC.handler = { _, _ in .object([:]) }
        await versionModel.connectToLocalEngine()
        check(!versionModel.enginePhase.isReady && versionModel.runtimeRevision == nil, "missing runtime metadata fails closed")
        let startupRPC = FakeRPC()
        let startup = model(startupRPC)
        let startupGate = Gate()
        startupRPC.connectHandler = { _ = await startupGate.response() }
        startupRPC.handler = { method, _ in
            if method == "fs/readFile" {
                let data = try JSONEncoder().encode(JSONValue.object([
                    "codexRevision": .string(CodexFeatureCatalog.upstreamRevision!)
                ]))
                return .object(["dataBase64": .string(data.base64EncodedString())])
            }
            if method == "account/login/start" {
                return .object(["type": .string("chatgpt"),
                                "loginId": .string("startup-login"),
                                "authUrl": .string("https://auth.openai.com/authorize")])
            }
            if method == "account/read" { return .object(["account": .null]) }
            return .object(["data": .array([])])
        }
        let viewStartup = Task { await startup.start() }
        await until { startupGate.continuation != nil }
        viewStartup.cancel() // SwiftUI can cancel its task when view identity changes.
        var reconnectStarted = false
        var reconnectFinished = false
        let reconnectDuringStartup = Task {
            reconnectStarted = true
            await startup.retryConnection()
            reconnectFinished = true
        }
        await until { reconnectStarted }
        check(!reconnectFinished && startupRPC.connectionAttempts == 1,
              "reconnect during startup waits for the existing connection attempt")
        startupGate.release(.null)
        await viewStartup.value
        await reconnectDuringStartup.value
        check(startup.enginePhase.isReady && startup.runtimeRevision == CodexFeatureCatalog.upstreamRevision,
              "cancelling the workspace task cannot strand model-owned engine startup")
        check(startupRPC.connectionAttempts == 1,
              "a cancelled startup caller and reconnect cannot create competing sockets")
        await startup.signInWithChatGPT()
        check(startup.pendingLoginID == "startup-login" && startup.loginURL?.host == "auth.openai.com",
              "sign-in becomes available after startup survives workspace cancellation")
        await startup.cancelSignIn()

        let unavailableRPC = FakeRPC()
        let unavailable = CodexWorkspaceModel(
            rpc: unavailableRPC, demoMode: false,
            preferences: UserDefaults(suiteName: "CodexPad.Regression.\(UUID().uuidString)")!,
            engineRetryWindow: .milliseconds(100)
        )
        let unavailableStarted = ContinuousClock.now
        unavailableRPC.connectHandler = { throw URLError(.cannotConnectToHost) }
        await unavailable.connectToLocalEngine()
        if case .offline(let message) = unavailable.enginePhase {
            check(unavailable.errorBanner == message,
                  "exhausted engine retries show an actionable error in Account settings")
        } else {
            preconditionFailure("An unreachable engine must finish offline")
        }
        check(unavailableRPC.connectionAttempts > 0 && unavailableRPC.calls.isEmpty &&
              unavailableStarted.duration(to: ContinuousClock.now) < .seconds(2),
              "a failed startup is bounded and cannot issue login or credential requests")

        let slowRPC = FakeRPC()
        let slowStartup = CodexWorkspaceModel(
            rpc: slowRPC, demoMode: false,
            preferences: UserDefaults(suiteName: "CodexPad.Regression.\(UUID().uuidString)")!,
            engineRetryDelay: { _ in .zero }
        )
        slowRPC.connectHandler = {
            if slowRPC.connectionAttempts <= 14 { throw URLError(.cannotConnectToHost) }
        }
        slowRPC.handler = startupRPC.handler
        await slowStartup.start()
        check(slowStartup.enginePhase.isReady && slowRPC.connectionAttempts == 15,
              "a slowly booting guest can initialize after the former ten-attempt limit")
        check(slowStartup.runtimeRevision == CodexFeatureCatalog.upstreamRevision,
              "extended startup still verifies the saved guest revision before readiness")
        check(slowStartup.runtimeLog.filter { $0.contains("transport code=-1004") }.count == 14,
              "warmup refusals retain their original numeric transport error")
        await slowStartup.signInWithChatGPT()
        check(slowStartup.pendingLoginID == "startup-login",
              "sign-in becomes available when a slowly booting local engine becomes ready")
        await slowStartup.cancelSignIn()

        let rpc = FakeRPC()
        let m = model(rpc)
        rpc.event("turn/started", turn("A", "turnA"))
        rpc.event("turn/started", turn("B", "turnB"))
        check(m.activeTurnID == "turnA", "background start cannot replace the selected active turn")
        await m.interruptTurn()
        check(rpc.calls.last?.1?["threadId"] == .string("A") && rpc.calls.last?.1?["turnId"] == .string("turnA"), "Stop targets the correct thread/turn pair")
        rpc.event("turn/completed", turn("B", "turnB", status: "completed"))
        check(m.isTurnRunning, "background completion cannot stop the selected turn")
        rpc.event("turn/diff/updated", .object(["threadId": .string("B"), "diff": .string("B diff")]))
        check(m.currentDiff.isEmpty, "background diff does not leak into selected changes")
        rpc.event("turn/plan/updated", .object(["threadId": .string("B"), "plan": .array([.object(["step": .string("B plan"), "status": .string("pending")])])]))
        check(m.plan.isEmpty, "background plan does not leak")
        m.composerText = "draft A"
        m.selectedThreadID = "B"
        check(m.composerText.isEmpty && m.currentDiff == "B diff" && m.plan.first?.text == "B plan", "thread switching restores scoped state")
        m.composerText = "draft B"
        m.selectedThreadID = "A"
        check(m.composerText == "draft A", "drafts survive thread switching")

        let gate = Gate()
        rpc.handler = { method, params in
            if method == "thread/resume" { return await gate.response() }
            return .object([:])
        }
        let resume = Task { await m.resumeThread("A") }
        await until { gate.continuation != nil }
        m.selectedThreadID = "B"
        rpc.event("item/started", .object(["threadId": .string("A"), "item": .object(["id": .string("live"), "type": .string("agentMessage"), "text": .string("Live")])]))
        gate.release(.object(["thread": .object(["id": .string("A"), "cwd": .string("/stale"), "turns": .array([])])]))
        await resume.value
        check(m.selectedThreadID == "B", "stale resume does not steal selection")
        check(m.timelineByThread["A"]?.first?.body == "Live", "stale resume does not erase live events")
        check(m.timelineByThread["A"]?.first?.state == .running, "started messages show Running, not Done")
        check(m.activeTurnID == nil, "resuming another thread cannot resurrect selected turn")

        let sendRPC = FakeRPC()
        let sender = model(sendRPC)
        sender.composerText = "Keep this draft"
        sendRPC.handler = { _, _ in throw CodexRPCError(code: -1, message: "rejected") }
        await sender.sendComposer()
        check(sender.composerText == "Keep this draft", "failed sends restore drafts")
        check(!sender.isTurnRunning && sender.selectedTimeline.isEmpty, "failed sends leave no false completed user message")
        sendRPC.handler = { method, params in
            if method == "turn/start" {
                sendRPC.event("turn/completed", turn("A", "fast", status: "completed"))
                return .object(["turn": .object(["id": .string("fast"), "status": .string("inProgress")])])
            }
            return .object([:])
        }
        await sender.sendComposer()
        check(!sender.isTurnRunning, "late turn/start response cannot resurrect a completed turn")
        check(sendRPC.calls.last?.1?["serviceTier"] == .null, "Provider default explicitly clears a previous service tier")

        let createRPC = FakeRPC()
        let creator = model(createRPC)
        creator.selectedThreadID = nil
        creator.composerText = "Only one thread"
        creator.selectedReasoningEffort = "low"
        creator.selectedServiceTier = "fast"
        let creation = Gate()
        createRPC.handler = { method, _ in
            if method == "thread/start" { return await creation.response() }
            return .object(["turn": .object(["id": .string("created-turn"), "status": .string("completed")])])
        }
        let first = Task { await creator.sendComposer() }
        await until { creation.continuation != nil }
        await creator.sendComposer()
        check(createRPC.calls.filter { $0.0 == "thread/start" }.count == 1, "double send cannot create duplicate threads")
        creator.composerText = "Next prompt typed while creating"
        creator.selectedReasoningEffort = "high"
        creator.selectedServiceTier = "flex"
        creation.release(.object(["thread": .object(["id": .string("new"), "cwd": .string("/root/workspace")])]))
        await first.value
        let createdTurn = createRPC.calls.last?.1
        check(createdTurn?["effort"] == .string("low") && createdTurn?["serviceTier"] == .string("fast"), "queued first send retains the controls selected when Send was clicked")
        check(creator.composerText == "Next prompt typed while creating", "first send preserves text typed during thread creation")
        creator.selectedThreadID = nil
        check(creator.composerText.isEmpty, "sending the initial draft does not leave a duplicate new-thread draft")

        let recoveryRPC = FakeRPC()
        let recovery = model(recoveryRPC)
        recovery.composerText = "Rejected prompt"
        let rejection = Gate()
        recoveryRPC.handler = { _, _ in
            _ = await rejection.response()
            throw CodexRPCError(code: -1, message: "rejected")
        }
        let rejectedSend = Task { await recovery.sendComposer() }
        await until { rejection.continuation != nil }
        recovery.composerText = "Newer unsent text"
        recovery.selectedThreadID = "B"
        recovery.composerText = "Unrelated B draft"
        rejection.release(.null)
        await rejectedSend.value
        check(recovery.composerText == "Unrelated B draft", "late send failure cannot replace another thread's draft")
        recovery.selectedThreadID = "A"
        check(recovery.composerText == "Rejected prompt\n\nNewer unsent text", "failed sends preserve both rejected text and a newer draft")

        let request: JSONValue = .object([
            "threadId": .string("A"), "availableDecisions": .array([.string("decline"), .string("cancel")])
        ])
        sendRPC.inboundHandler?(.request(id: .integer(42), method: "item/commandExecution/requestApproval", params: request))
        let approval = sender.pendingRequests[0]
        await sender.answerCommandDecision(approval, decision: .string("accept"))
        check(sender.pendingRequests.count == 1, "unsupported approval choices cannot be sent")
        await sender.answerCommandDecision(approval, decision: .string("cancel"))
        check(sender.pendingRequests.isEmpty && sendRPC.responses.last?["decision"] == .string("cancel"), "advertised approval decision round trips exactly")

        sendRPC.stateHandler?(.failed("socket closed"))
        check(!sender.enginePhase.isReady && !sender.isTurnRunning, "socket failure clears misleading ready/running UI")
        sender.workspacePath = "/root/workspaces/codexpad-files"
        await sender.unlinkFilesFolder()
        check(sender.workspacePath == "/root/workspaces/codexpad-files", "offline unlink cannot falsely claim saved mount was removed")
        sender.composerText = "offline draft"
        let count = sendRPC.calls.count
        await sender.sendComposer()
        check(sendRPC.calls.count == count && sender.composerText == "offline draft", "offline send neither calls RPC nor loses text")

        let catalogRPC = FakeRPC()
        let catalog = model(catalogRPC)
        let firstModel: JSONValue = .object(["id": .string("first"), "model": .string("provider-first")])
        let nextModel: JSONValue = .object([
            "id": .string("future-model"), "model": .string("provider-future"), "isDefault": .bool(true),
            "hidden": .bool(true), "defaultReasoningEffort": .string("high"),
            "supportedReasoningEfforts": .array([.object(["reasoningEffort": .string("high")])])
        ])
        catalogRPC.handler = { method, params in
            guard method == "model/list" else { return .object([:]) }
            if params?["cursor"]?.stringValue == nil {
                return .object(["data": .array([firstModel]), "nextCursor": .string("page-two")])
            }
            return .object(["data": .array([firstModel, nextModel]), "nextCursor": .null])
        }
        await catalog.refreshModels()
        check(catalog.availableModels.map(\.id) == ["first", "future-model"], "model catalog paginates and deduplicates provider entries")
        check(catalog.selectedModelID == "future-model" && catalog.selectedReasoningEffort == "high", "new provider models retain their default reasoning without hard-coded names")
        check(catalog.availableModels.last?.hidden == true, "hidden provider models remain available to the complete picker")
        catalogRPC.handler = { _, _ in .object(["data": .array([]), "nextCursor": .string("loop")]) }
        await catalog.refreshModels()
        check(catalog.availableModels.count == 2 && catalog.errorBanner != nil, "invalid model pagination preserves the previous usable catalog")

        let historyRPC = FakeRPC()
        let history = model(historyRPC)
        func message(_ id: String) -> JSONValue {
            .object(["id": .string(id), "type": .string("agentMessage"), "text": .string(id)])
        }
        historyRPC.handler = { method, _ in
            guard method == "thread/resume" else { return .object([:]) }
            return .object([
                "thread": .object(["id": .string("A"), "cwd": .string("/root/A"),
                    "turns": .array([.object(["items": .array([message("current")])])])]),
                "turnsBackwardsCursor": .string("older-page")
            ])
        }
        await history.resumeThread("A")
        check(history.hasEarlierHistory, "resume exposes the upstream history cursor")
        let historyGate = Gate()
        historyRPC.handler = { method, params in
            if method == "thread/turns/list" {
                check(params?["itemsView"] == .string("full") && params?["sortDirection"] == .string("desc"), "history asks for complete backward-paginated turns")
                return await historyGate.response()
            }
            return .object([:])
        }
        let loading = Task { await history.loadEarlierHistory() }
        await until { historyGate.continuation != nil }
        history.selectedThreadID = "B"
        historyGate.release(.object(["nextCursor": .null, "data": .array([
            .object(["items": .array([message("middle"), message("current")])]),
            .object(["items": .array([message("oldest"), message("oldest")])])
        ])]))
        await loading.value
        check(history.selectedThreadID == "B" && history.selectedTimeline.isEmpty, "late history pages stay scoped to their original thread")
        check(history.timelineByThread["A"]?.map(\.id) == ["oldest", "middle", "current"], "history is chronological and deduplicates both page and live items")
        history.selectedThreadID = "A"
        check(!history.hasEarlierHistory && !history.isLoadingHistory, "history clears its completed cursor and loading state")

        let filesRPC = FakeRPC()
        let files = model(filesRPC)
        files.workspacePath = "/root/previous"
        filesRPC.handler = { _, params in
            let command = params?["command"]?.arrayValue?.first?.stringValue
            return .object(["exitCode": .integer(command == "/bin/mountpoint" ? 0 : 1)])
        }
        await files.chooseFilesFolder()
        check(filesRPC.calls.count == 1 && files.workspacePath == "/root/previous", "choosing another folder never unmounts live Files access")
        files.linkedFolderPhase = .disconnected
        filesRPC.calls.removeAll()
        filesRPC.handler = { _, params in
            let command = params?["command"]?.arrayValue?.first?.stringValue
            return .object(["exitCode": .integer(command == "/bin/mkdir" ? 0 : 1), "stderr": .string("Picker cancelled")])
        }
        await files.chooseFilesFolder()
        check(files.linkedFolderPhase == .disconnected && files.workspacePath == "/root/previous", "cancelled Files selection preserves the previous workspace and link state")
        check(!filesRPC.calls.contains { $0.1?["command"]?.arrayValue?.first == .string("/bin/umount") }, "folder selection never silently revokes a bookmark")

        let loginRPC = FakeRPC()
        let login = model(loginRPC)
        var loginIndex = 0
        loginRPC.handler = { method, params in
            if method == "account/login/start" {
                loginIndex += 1
                let type = params?["type"]?.stringValue ?? ""
                if type == "apiKey" { return .object(["type": .string(type)]) }
                return .object(["type": .string(type), "loginId": .string("login-\(loginIndex)"),
                                "authUrl": .string("https://auth.openai.com/authorize"),
                                "verificationUrl": .string("https://auth.openai.com/codex/device"),
                                "userCode": .string("ABCD-1234")])
            }
            return .object(["data": .array([])])
        }
        await login.signInWithChatGPT()
        check(login.isSigningIn && login.loginURL != nil && login.pendingLoginID == "login-1",
              "browser login keeps an identifiable pending attempt")
        await login.signInWithDeviceCode()
        check(loginIndex == 1, "duplicate sign-in cannot replace the pending attempt")
        await login.cancelSignIn()
        check(!login.isSigningIn && login.loginURL == nil && loginRPC.calls.last?.1?["loginId"] == .string("login-1"),
              "cancellation closes browser and cancels the matching server login")
        await login.signInWithDeviceCode()
        loginRPC.event("account/login/completed", .object(["loginId": .string("login-1"), "success": .bool(false), "error": .string("old failure")]))
        check(login.deviceCode == "ABCD-1234" && login.pendingLoginID == "login-2" && login.errorBanner == nil,
              "stale completion cannot erase a newer device-code attempt")
        login.reopenSignInBrowser()
        check(login.loginURL?.host == "auth.openai.com", "device verification opens the in-app sign-in browser")
        login.closeSignInBrowser()
        check(login.isSigningIn && login.deviceCode != nil, "closing verification preserves the pending device-code poll")
        loginRPC.event("account/login/completed", .object(["loginId": .string("login-2"), "success": .bool(false), "error": .string("Device login disabled")]))
        check(!login.isSigningIn && login.deviceCode == nil && login.errorBanner == "Device login disabled",
              "matching failures clear pending state and show the actual server error")
        let savedKey = await login.signIn(apiKey: "  fixture-key\n")
        check(savedKey && loginRPC.calls.contains { $0.0 == "account/login/start" && $0.1?["apiKey"] == .string("fixture-key") },
              "API key sign-in trims pasted whitespace")
        let loginGate = Gate()
        loginRPC.handler = { method, _ in
            if method == "account/login/start" { return await loginGate.response() }
            return .object([:])
        }
        let startingLogin = Task { await login.signInWithChatGPT() }
        await until { loginGate.continuation != nil }
        await login.cancelSignIn()
        loginGate.release(.object(["type": .string("chatgpt"), "loginId": .string("late-login"), "authUrl": .string("https://auth.openai.com/authorize")]))
        await startingLogin.value
        check(!login.isSigningIn && login.loginURL == nil && loginRPC.calls.last?.1?["loginId"] == .string("late-login"),
              "cancelled in-flight start cancels its late server login without reopening a browser")
        loginRPC.handler = { _, _ in .object(["type": .string("chatgpt")]) }
        await login.signInWithChatGPT()
        check(!login.isSigningIn && login.errorBanner?.contains("login ID") == true,
              "malformed server response produces an actionable error instead of doing nothing")
        loginRPC.handler = { method, _ in
            if method == "account/login/start" {
                loginRPC.event("account/login/completed", .object(["loginId": .null, "success": .bool(true)]))
                return .object(["type": .string("apiKey")])
            }
            return .object(["data": .array([])])
        }
        let earlyKey = await login.signIn(apiKey: "fixture-key")
        check(earlyKey && !login.isSigningIn, "API key notification before the RPC response cannot turn a valid save into a malformed login")
        loginRPC.handler = { method, _ in
            if method == "account/login/start" {
                loginRPC.event("account/login/completed", .object(["loginId": .string("early-login"), "success": .bool(false), "error": .string("early failure")]))
                return .object(["type": .string("chatgpt"), "loginId": .string("early-login"), "authUrl": .string("https://auth.openai.com/authorize")])
            }
            return .object(["data": .array([])])
        }
        await login.signInWithChatGPT()
        check(!login.isSigningIn && login.loginURL == nil && login.errorBanner == "early failure",
              "managed completion before the start response still closes the matching login")
        let loginPrivacyRPC = FakeRPC()
        let loginPrivacy = model(loginPrivacyRPC)
        let privateError = "https://auth.example/authorize?state=private-state&code=private-code fixture@example.invalid"
        let privateNotice = "https://auth.example/authorize?state=notice-state&code=notice-code notice@example.invalid"
        let privateMarkers = ["private-state", "private-code", "fixture@example.invalid",
                              "notice-state", "notice-code", "notice@example.invalid"]
        loginPrivacyRPC.handler = { _, _ in throw CodexRPCError(code: -32000, message: privateError) }
        await loginPrivacy.signInWithChatGPT()
        check(loginPrivacy.errorBanner?.contains(privateError) == true,
              "authentication errors remain actionable in the UI")
        check(loginPrivacy.runtimeLog.contains("authentication.start.failed rpc code=-32000") &&
              privateMarkers.allSatisfy { !loginPrivacy.runtimeLog.joined().contains($0) },
              "authentication diagnostics contain numeric error codes without private server messages")
        check(codexDiagnosticFailure(URLError(.cannotConnectToHost)) == "transport code=-1004",
              "transport errors can be distinguished from RPC rejections without logging URLs")
        let existingLoginError = loginPrivacy.errorBanner
        loginPrivacyRPC.event("warning", .object(["message": .string(privateNotice)]))
        check(loginPrivacy.errorBanner == existingLoginError,
              "server warnings preserve an existing login error banner")
        check(loginPrivacy.runtimeLog.contains("server.notice method=warning") &&
              privateMarkers.allSatisfy { !loginPrivacy.runtimeLog.joined().contains($0) },
              "warnings with an existing error omit private messages from diagnostic logs")

        let noticeRPC = FakeRPC()
        let notice = model(noticeRPC)
        check(notice.errorBanner == nil, "warning visibility fixture starts without an error banner")
        noticeRPC.event("warning", .object(["message": .string(privateNotice)]))
        check(notice.errorBanner == privateNotice,
              "server notices appear in the UI when no error banner exists")
        check(notice.runtimeLog.contains("server.notice method=warning") &&
              privateMarkers.allSatisfy { !notice.runtimeLog.joined().contains($0) },
              "visible server notices omit private messages from diagnostic logs")
        let authRecoveryRPC = FakeRPC()
        let authRecovery = model(authRecoveryRPC)
        var recoveredAccount = false
        authRecoveryRPC.handler = { method, _ in
            if method == "fs/readFile" {
                let data = try JSONEncoder().encode(JSONValue.object(["codexRevision": .string(CodexFeatureCatalog.upstreamRevision!)]))
                return .object(["dataBase64": .string(data.base64EncodedString())])
            }
            if method == "account/read", recoveredAccount {
                return .object(["account": .object(["type": .string("chatgpt"), "email": .string("fixture@example.invalid"), "planType": .string("plus")])])
            }
            return .object(["data": .array([])])
        }
        await authRecovery.start()
        recoveredAccount = true
        authRecoveryRPC.stateHandler?(.disconnected)
        await authRecovery.applicationDidBecomeActive()
        check(authRecovery.enginePhase.isReady && authRecovery.account.isAuthenticated,
              "foreground recovery reconnects and reloads saved authentication when completion notifications were missed")
        let keyGate = Gate()
        loginRPC.handler = { method, _ in
            if method == "account/login/start" { return await keyGate.response() }
            return .object(["data": .array([])])
        }
        let savingKey = Task { await login.signIn(apiKey: "fixture-key") }
        await until { keyGate.continuation != nil }
        loginRPC.event("account/login/completed", .object(["loginId": .null, "success": .bool(true)]))
        check(login.isSigningIn, "uncorrelated API-key notification cannot unlock an in-flight save")
        await login.signInWithChatGPT()
        check(login.pendingLoginID == nil && login.isSigningIn, "a second login remains blocked until the API-key request finishes")
        keyGate.release(.object(["type": .string("apiKey")]))
        _ = await savingKey.value
        login.enginePhase = .offline(message: "fixture offline")
        loginRPC.calls.removeAll()
        await login.signInWithDeviceCode()
        check(loginRPC.calls.isEmpty && login.errorBanner?.contains("Reconnect") == true,
              "offline login fails visibly without sending a broken request")

        let demo = CodexWorkspaceModel(demoMode: true)
        await demo.start()
        demo.composerText = "fixture send"
        await demo.sendComposer()
        check(demo.errorBanner == nil && !demo.isTurnRunning, "demo sends have a working isolated transport")
        check(demo.selectedTimeline.last?.body.contains("Demo response received") == true, "demo response is explicitly labelled as simulated")
        print("\(checks) model regression assertions passed")
    }
}
