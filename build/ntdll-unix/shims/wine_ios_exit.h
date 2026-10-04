/* Override exit() for Wine on iOS to prevent killing the app.
 * Uses longjmp back to the Wine process/child thread when called,
 * falls back to pthread_exit for threads that haven't set up a jmpbuf. */
#ifdef WINE_IOS
#include <os/log.h>
#include <pthread.h>
#include <setjmp.h>

/* Thread-local: each Wine "process" thread has its own jmpbuf */
extern _Thread_local jmp_buf wine_ios_exit_jmpbuf;
extern _Thread_local volatile int wine_ios_exit_code;
extern _Thread_local pthread_t wine_ios_main_thread;
extern _Thread_local int wine_ios_exit_initialized;

static inline __attribute__((noreturn)) void wine_ios_exit(int status) {
    os_log_error(OS_LOG_DEFAULT, "[Wine ntdll] exit(%d) intercepted on iOS (tid=%p)",
                 status, (void*)pthread_self());
    if (wine_ios_exit_initialized && pthread_equal(pthread_self(), wine_ios_main_thread)) {
        wine_ios_exit_code = status;
        longjmp(wine_ios_exit_jmpbuf, 1);
    }
    /* Thread without jmpbuf or different thread — just terminate this thread */
    pthread_exit(NULL);
    __builtin_unreachable();
}

#define exit(x) wine_ios_exit(x)
#endif

/* config.h comes from wine/build-macos, configured on a macOS 27 host, so it has
 * HAVE_PIPE2. pipe2() is iOS 27 only (SDK: __API_AVAILABLE(ios(27.0))): the app
 * targets iOS 17 and weak-links it, so on iPadOS 26.6 it is NULL and the first
 * server_pipe() of every Wine session called address 0 (two crash reports, an
 * M5 iPad, 2026-10-04). Wine's pipe() + FD_CLOEXEC fallback works everywhere. */
#undef HAVE_PIPE2
