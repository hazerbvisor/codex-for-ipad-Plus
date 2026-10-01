// Included in the staged Roots.m. fakefs stores regular-file data at data/<path>.
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
    if (CodexPadRootHasRuntime([roots rootUrl:roots.defaultRoot])) return YES;
    // Retain every existing root. Repair by importing a separate filesystem,
    // never by overwriting projects, credentials or the fakefs database.
    NSString *previous = roots.defaultRoot;
    NSString *name = @"codexpad-arm64";
    for (NSUInteger index = 1; [roots.roots containsObject:name]; index++)
        name = [NSString stringWithFormat:@"codexpad-arm64-%lu", (unsigned long)index];
    NSURL *archive = [NSBundle.mainBundle.bundleURL URLByAppendingPathComponent:@"codexpad-runtime.tar.gz"];
    if (![NSFileManager.defaultManager fileExistsAtPath:archive.path] ||
        ![roots importRootFromArchive:archive name:name error:error progressReporter:nil]) return NO;
    if (!CodexPadRootHasRuntime([roots rootUrl:name])) {
        if (error) *error = [NSError errorWithDomain:@"CodexPadRuntime" code:1
            userInfo:@{NSLocalizedDescriptionKey: @"The bundled runtime imported without the Codex server or OpenRC files."}];
        return NO;
    }
    roots.defaultRoot = name;
    NSLog(@"CodexPad selected runtime %@; previous filesystem %@ retained", name, previous);
    return YES;
}
