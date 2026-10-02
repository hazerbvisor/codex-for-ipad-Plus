// Included in the staged Roots.m. fakefs stores regular-file data at data/<path>.
#include "CodexPadRuntimeUpgrade.c"
static BOOL CodexPadRootHasRuntime(NSURL *root) {
    NSURL *data = [root URLByAppendingPathComponent:@"data" isDirectory:YES];
    NSFileManager *manager = NSFileManager.defaultManager;
    for (NSString *path in @[@"usr/local/libexec/codexpad/codex-app-server",
                             @"etc/init.d/codexpad", @"sbin/openrc", @"sbin/rc-service"]) {
        NSDictionary *attributes = [manager attributesOfItemAtPath:
            [data URLByAppendingPathComponent:path].path error:nil];
        if (![attributes[NSFileType] isEqual:NSFileTypeRegular] ||
            [attributes[NSFileSize] unsignedLongLongValue] == 0) return NO;
    }
    NSData *manifestData = [NSData dataWithContentsOfURL:
        [data URLByAppendingPathComponent:@"usr/local/share/codexpad/runtime.json"]];
    NSDictionary *manifest = manifestData ? [NSJSONSerialization JSONObjectWithData:manifestData options:0 error:nil] : nil;
    return [manifest isKindOfClass:NSDictionary.class] &&
        [manifest[@"hasAppServer"] isKindOfClass:NSNumber.class] &&
        [manifest[@"hasAppServer"] boolValue] &&
        [manifest[@"target"] isEqual:@"aarch64-unknown-linux-musl"];
}

BOOL CodexPadPrepareRuntimeRoot(Roots *roots, NSError **error) {
    NSData *pinsData = [NSData dataWithContentsOfURL:
        [NSBundle.mainBundle.bundleURL URLByAppendingPathComponent:@"upstreams.json"]];
    id pinsObject = pinsData ? [NSJSONSerialization JSONObjectWithData:pinsData options:0 error:nil] : nil;
    NSDictionary *pins = [pinsObject isKindOfClass:NSDictionary.class] ? pinsObject : nil;
    id codex = [pins[@"codex"] isKindOfClass:NSDictionary.class] ? pins[@"codex"] : nil;
    id release = [codex[@"codeModeHostRelease"] isKindOfClass:NSDictionary.class] ? codex[@"codeModeHostRelease"] : nil;
    NSString *hash = [release[@"binarySha256"] isKindOfClass:NSString.class] ? release[@"binarySha256"] : nil;
    NSURL *selected = [roots rootUrl:roots.defaultRoot];
    if (CodexPadRootHasRuntime(selected)) {
        // Upgrade the existing guest in place, retaining the selected root and
        // all saved account state/projects. Only the missing helper is installed.
        NSDictionary *installed = [NSJSONSerialization JSONObjectWithData:
            [NSData dataWithContentsOfURL:[selected URLByAppendingPathComponent:@"data/usr/local/share/codexpad/runtime.json"]]
            options:0 error:nil];
        int status = EINVAL;
        if ([installed[@"codexRevision"] isEqual:codex[@"revision"]] && hash) {
            if (CodexPadRootHasCodeModeHost(selected.fileSystemRepresentation, hash.UTF8String)) return YES;
            NSURL *archive = [NSBundle.mainBundle.bundleURL URLByAppendingPathComponent:@"root.tar.gz"];
            status = CodexPadInstallCodeModeHost(archive.fileSystemRepresentation, selected.fileSystemRepresentation,
                                               hash.UTF8String);
        }
        if (status || !CodexPadRootHasCodeModeHost(selected.fileSystemRepresentation, hash.UTF8String)) {
            if (error) *error = [NSError errorWithDomain:@"CodexPadRuntime" code:status ?: EIO
                userInfo:@{NSLocalizedDescriptionKey: @"The Code Mode helper could not be installed into the existing runtime. Its Codex version must match the bundled release; existing files and credentials were retained."}];
            return NO;
        }
        NSLog(@"CodexPad runtime Code Mode helper installed");
        return YES;
    }
    // Retain every existing root. Repair by importing a separate filesystem,
    // never by overwriting projects, credentials or the fakefs database.
    NSString *previous = roots.defaultRoot;
    NSString *name = @"codexpad-arm64";
    for (NSUInteger index = 1; [roots.roots containsObject:name]; index++)
        name = [NSString stringWithFormat:@"codexpad-arm64-%lu", (unsigned long)index];
    NSURL *archive = [NSBundle.mainBundle.bundleURL URLByAppendingPathComponent:@"root.tar.gz"];
    if (![NSFileManager.defaultManager fileExistsAtPath:archive.path] ||
        ![roots importRootFromArchive:archive name:name error:error progressReporter:nil]) return NO;
    if (!CodexPadRootHasRuntime([roots rootUrl:name]) ||
            !CodexPadRootHasCodeModeHost([roots rootUrl:name].fileSystemRepresentation, hash.UTF8String)) {
        if (error) *error = [NSError errorWithDomain:@"CodexPadRuntime" code:1
            userInfo:@{NSLocalizedDescriptionKey: @"The bundled runtime imported without the Codex server, matching Code Mode helper or OpenRC files."}];
        return NO;
    }
    roots.defaultRoot = name;
    NSLog(@"CodexPad selected runtime %@; previous filesystem %@ retained", name, previous);
    return YES;
}
