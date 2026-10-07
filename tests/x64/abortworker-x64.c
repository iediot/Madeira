/* A worker thread ends the process while the first thread is busy or parked.
 *
 * The shape of RimWorld's crash on the device: Mono's unhandled-exception path
 * calls TerminateProcess(GetCurrentProcess(), code) on a worker. Madeira must
 * end the session then (the library comes back), whichever thread ends the
 * process. Modes (first argument):
 *   running  the first thread keeps making server requests (the device run)
 *   parked   the first thread waits forever on an event nobody sets
 * Build: tests/x64/build.sh abortworker-x64 (or the llvm-mingw clang directly). */
#include <windows.h>
#include <stdio.h>
#include <string.h>

static DWORD WINAPI end_process(void *arg)
{
    (void)arg;
    Sleep(3000);
    printf("abortworker: thread %04lx ends the process with 0x67fe2394\n", GetCurrentThreadId());
    fflush(stdout);
    TerminateProcess(GetCurrentProcess(), 0x67fe2394);
    return 0;
}

int main(int argc, char **argv)
{
    const char *mode = argc > 1 ? argv[1] : "running";
    HANDLE never = CreateEventW(NULL, TRUE, FALSE, NULL);

    printf("abortworker: first thread %04lx, mode %s\n", GetCurrentThreadId(), mode);
    fflush(stdout);
    CloseHandle(CreateThread(NULL, 0, end_process, NULL, 0, NULL));
    if (!strcmp(mode, "parked"))
        WaitForSingleObject(never, INFINITE);
    else
        for (;;)
        {
            CloseHandle(CreateEventW(NULL, FALSE, FALSE, NULL));
            Sleep(1);
        }
    return 0;
}
