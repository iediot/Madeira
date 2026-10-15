#ifndef MADEIRA_RUNTIME_DIAGNOSTICS_POLICY_H
#define MADEIRA_RUNTIME_DIAGNOSTICS_POLICY_H
#include <stdlib.h>
#include <errno.h>
#include <string.h>

static int ios_diagnostics_lean(const char *value)
{
    return value && *value && strcmp(value, "0");
}

static unsigned ios_diagnostics_sweep_cycles(const char *value, const char *lean)
{
    char *end;
    unsigned long n;
    unsigned fallback = ios_diagnostics_lean(lean) ? 300 : 30;
    if (!value || !*value || *value == '-') return fallback;
    errno = 0;
    n = strtoul(value, &end, 10);
    return !errno && !*end && n >= 1 && n <= 100000 ? (unsigned)n : fallback;
}
#endif
