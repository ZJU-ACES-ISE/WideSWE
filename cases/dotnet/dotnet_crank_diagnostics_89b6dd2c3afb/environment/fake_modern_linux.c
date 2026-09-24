#define _GNU_SOURCE

#include <dlfcn.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdbool.h>
#include <string.h>
#include <sys/utsname.h>

static const char *fake_release_path = "/tmp/ecosync-kernel-osrelease";
static const char *real_release_path = "/proc/sys/kernel/osrelease";

static bool is_release_path(const char *path)
{
    return path != NULL && strcmp(path, real_release_path) == 0;
}

int open(const char *path, int flags, ...)
{
    static int (*real_open)(const char *, int, ...) = NULL;
    mode_t mode = 0;
    if (flags & O_CREAT) {
        va_list args;
        va_start(args, flags);
        mode = va_arg(args, mode_t);
        va_end(args);
    }
    if (real_open == NULL) {
        real_open = dlsym(RTLD_NEXT, "open");
    }
    path = is_release_path(path) ? fake_release_path : path;
    return (flags & O_CREAT) ? real_open(path, flags, mode) : real_open(path, flags);
}

int open64(const char *path, int flags, ...)
{
    static int (*real_open64)(const char *, int, ...) = NULL;
    mode_t mode = 0;
    if (flags & O_CREAT) {
        va_list args;
        va_start(args, flags);
        mode = va_arg(args, mode_t);
        va_end(args);
    }
    if (real_open64 == NULL) {
        real_open64 = dlsym(RTLD_NEXT, "open64");
    }
    path = is_release_path(path) ? fake_release_path : path;
    return (flags & O_CREAT) ? real_open64(path, flags, mode) : real_open64(path, flags);
}

int openat(int dirfd, const char *path, int flags, ...)
{
    static int (*real_openat)(int, const char *, int, ...) = NULL;
    mode_t mode = 0;
    if (flags & O_CREAT) {
        va_list args;
        va_start(args, flags);
        mode = va_arg(args, mode_t);
        va_end(args);
    }
    if (real_openat == NULL) {
        real_openat = dlsym(RTLD_NEXT, "openat");
    }
    path = is_release_path(path) ? fake_release_path : path;
    return (flags & O_CREAT) ? real_openat(dirfd, path, flags, mode) : real_openat(dirfd, path, flags);
}

int uname(struct utsname *buffer)
{
    static int (*real_uname)(struct utsname *) = NULL;
    if (real_uname == NULL) {
        real_uname = dlsym(RTLD_NEXT, "uname");
    }
    int result = real_uname(buffer);
    if (result == 0) {
        const char release[] = "6.8.0-ecosync";
        memcpy(buffer->release, release, sizeof(release));
    }
    return result;
}
