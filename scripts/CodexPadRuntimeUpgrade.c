// Included by Roots.m before guest mount. Install only the matching managed
// helper; never replace a root, service, user file, credential or configuration.
#include <archive.h>
#include <archive_entry.h>
#include <errno.h>
#include <fcntl.h>
#include <sqlite3.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <unistd.h>
#include "fs/fake-db.h"
#ifdef __APPLE__
#include <CommonCrypto/CommonDigest.h>
#define SHA256_CTX CC_SHA256_CTX
#define SHA256_Init CC_SHA256_Init
#define SHA256_Update CC_SHA256_Update
#define SHA256_Final CC_SHA256_Final
#else
#define OPENSSL_API_COMPAT 0x10100000L
#include <openssl/sha.h>
#endif

#define CODEXPAD_HOST_PATH "/usr/local/libexec/codexpad/codex-code-mode-host"

static int CodexPadHostHashMatches(int fd, const char *expectedHash) {
    if (!expectedHash || lseek(fd, 0, SEEK_SET) < 0) return 0;
    SHA256_CTX hash;
    SHA256_Init(&hash);
    unsigned char block[65536], digest[32];
    ssize_t size;
    while ((size = read(fd, block, sizeof(block))) > 0) SHA256_Update(&hash, block, (unsigned int)size);
    if (size < 0) return 0;
    SHA256_Final(digest, &hash);
    char digestText[65];
    for (size_t i = 0; i < sizeof(digest); i++) snprintf(digestText + i * 2, 3, "%02x", digest[i]);
    return strcmp(digestText, expectedHash) == 0;
}

static int CodexPadHostDirectory(const char *root) {
    int fd = open(root, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
    const char *parts[] = {"data", "usr", "local", "libexec", "codexpad"};
    for (size_t i = 0; fd >= 0 && i < sizeof(parts) / sizeof(parts[0]); i++) {
        int next = openat(fd, parts[i], O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
        close(fd);
        fd = next;
    }
    return fd;
}

static int CodexPadOpenMetadata(const char *root, sqlite3 **db) {
    char path[4096];
    if (snprintf(path, sizeof(path), "%s/meta.db", root) >= (int)sizeof(path)) return ENAMETOOLONG;
    struct stat st;
    if (lstat(path, &st) != 0 || !S_ISREG(st.st_mode)) return EINVAL;
    if (sqlite3_open_v2(path, db, SQLITE_OPEN_READWRITE, NULL) != SQLITE_OK) return EIO;
    sqlite3_busy_timeout(*db, 5000);
    return 0;
}

static int CodexPadRootHasCodeModeHost(const char *root, const char *expectedHash) {
    int directory = CodexPadHostDirectory(root);
    if (directory < 0) return 0;
    int fd = openat(directory, "codex-code-mode-host", O_RDONLY | O_NOFOLLOW);
    close(directory);
    struct stat st;
    unsigned char header[20];
    int valid = fd >= 0 && fstat(fd, &st) == 0 && S_ISREG(st.st_mode) && st.st_size >= 64 &&
        read(fd, header, sizeof(header)) == sizeof(header) &&
        memcmp(header, "\177ELF\002\001", 6) == 0 && header[18] == 183 && header[19] == 0 &&
        CodexPadHostHashMatches(fd, expectedHash);
    if (fd >= 0) close(fd);
    if (!valid) return 0;
    sqlite3 *db = NULL;
    sqlite3_stmt *query = NULL;
    valid = CodexPadOpenMetadata(root, &db) == 0 &&
        sqlite3_prepare_v2(db, "SELECT stat FROM paths JOIN stats USING(inode) WHERE path=?", -1, &query, NULL) == SQLITE_OK &&
        sqlite3_bind_blob(query, 1, CODEXPAD_HOST_PATH, strlen(CODEXPAD_HOST_PATH), SQLITE_STATIC) == SQLITE_OK &&
        sqlite3_step(query) == SQLITE_ROW && sqlite3_column_bytes(query, 0) == sizeof(struct ish_stat);
    if (valid) {
        struct ish_stat metadata;
        memcpy(&metadata, sqlite3_column_blob(query, 0), sizeof(metadata));
        valid = S_ISREG(metadata.mode) && (metadata.mode & 0100);
    }
    sqlite3_finalize(query);
    if (db) sqlite3_close(db);
    return valid;
}

static int CodexPadInstallCodeModeHost(const char *archivePath, const char *root, const char *expectedHash) {
    if (CodexPadRootHasCodeModeHost(root, expectedHash)) return 0;
    int result = EIO, fd = -1, directory = CodexPadHostDirectory(root);
    if (directory < 0) return errno;
    char temporary[64];
    snprintf(temporary, sizeof(temporary), ".codexpad-helper-%ld-%u", (long)getpid(), arc4random());
    fd = openat(directory, temporary, O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW, 0700);
    struct archive *archive = archive_read_new();
    sqlite3 *db = NULL;
    sqlite3_stmt *statement = NULL;
    if (fd < 0 || !archive) goto cleanup;
    archive_read_support_filter_gzip(archive);
    archive_read_support_format_tar(archive);
    if (archive_read_open_filename(archive, archivePath, 1024 * 1024) != ARCHIVE_OK) goto cleanup;
    struct archive_entry *entry;
    int status, found = 0;
    while ((status = archive_read_next_header(archive, &entry)) == ARCHIVE_OK) {
        const char *name = archive_entry_pathname(entry);
        if (strncmp(name, "./", 2) == 0) name += 2;
        if (strcmp(name, &CODEXPAD_HOST_PATH[1]) != 0) continue;
        if (found++ || archive_entry_filetype(entry) != AE_IFREG || archive_entry_hardlink(entry) ||
                archive_entry_symlink(entry) || archive_entry_size(entry) < 64 ||
                !(archive_entry_perm(entry) & 0100)) goto cleanup;
        char block[65536];
        ssize_t size;
        while ((size = archive_read_data(archive, block, sizeof(block))) > 0) {
            ssize_t done = 0;
            while (done < size) {
                ssize_t wrote = write(fd, block + done, size - done);
                if (wrote < 0 && errno == EINTR) continue;
                if (wrote <= 0) goto cleanup;
                done += wrote;
            }
        }
        if (size < 0) goto cleanup;
    }
    if (status != ARCHIVE_EOF || found != 1 || fchmod(fd, 0755) != 0 || fsync(fd) != 0) goto cleanup;
    unsigned char header[20];
    if (!CodexPadHostHashMatches(fd, expectedHash) ||
            pread(fd, header, sizeof(header), 0) != sizeof(header) ||
            memcmp(header, "\177ELF\002\001", 6) != 0 || header[18] != 183 || header[19] != 0) goto cleanup;
    close(fd); fd = -1;
    // fakefs needs both host file data and a guest metadata row. A host copy
    // alone is invisible to the guest. Work happens before mount/first process.
    if (CodexPadOpenMetadata(root, &db) != 0 ||
            sqlite3_exec(db, "BEGIN IMMEDIATE", NULL, NULL, NULL) != SQLITE_OK) goto cleanup;
    const char *parent = "/usr/local/libexec/codexpad";
    if (sqlite3_prepare_v2(db, "SELECT stat FROM paths JOIN stats USING(inode) WHERE path=?", -1, &statement, NULL) != SQLITE_OK ||
            sqlite3_bind_blob(statement, 1, parent, strlen(parent), SQLITE_STATIC) != SQLITE_OK ||
            sqlite3_step(statement) != SQLITE_ROW || sqlite3_column_bytes(statement, 0) != sizeof(struct ish_stat)) goto cleanup;
    struct ish_stat parentMetadata;
    memcpy(&parentMetadata, sqlite3_column_blob(statement, 0), sizeof(parentMetadata));
    if (!S_ISDIR(parentMetadata.mode)) goto cleanup;
    sqlite3_finalize(statement); statement = NULL;
    struct ish_stat metadata = {.mode = S_IFREG | 0755, .uid = 0, .gid = 0};
    if (sqlite3_prepare_v2(db, "INSERT INTO stats(stat) VALUES(?)", -1, &statement, NULL) != SQLITE_OK ||
            sqlite3_bind_blob(statement, 1, &metadata, sizeof(metadata), SQLITE_STATIC) != SQLITE_OK ||
            sqlite3_step(statement) != SQLITE_DONE) goto cleanup;
    sqlite3_finalize(statement); statement = NULL;
    sqlite3_int64 inode = sqlite3_last_insert_rowid(db);
    if (sqlite3_prepare_v2(db, "INSERT OR REPLACE INTO paths(path,inode) VALUES(?,?)", -1, &statement, NULL) != SQLITE_OK ||
            sqlite3_bind_blob(statement, 1, CODEXPAD_HOST_PATH, strlen(CODEXPAD_HOST_PATH), SQLITE_STATIC) != SQLITE_OK ||
            sqlite3_bind_int64(statement, 2, inode) != SQLITE_OK ||
            sqlite3_step(statement) != SQLITE_DONE ||
            renameat(directory, temporary, directory, "codex-code-mode-host") != 0 ||
            sqlite3_exec(db, "COMMIT", NULL, NULL, NULL) != SQLITE_OK) goto cleanup;
    // If interrupted between rename and commit, the metadata check above fails
    // and the next launch safely retries this same single-file installation.
    result = 0;
cleanup:
    sqlite3_finalize(statement);
    if (db) { if (result) sqlite3_exec(db, "ROLLBACK", NULL, NULL, NULL); sqlite3_close(db); }
    if (fd >= 0) close(fd);
    if (archive) archive_read_free(archive);
    unlinkat(directory, temporary, 0);
    close(directory);
    return result;
}
