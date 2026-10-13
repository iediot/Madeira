/* Bounded guest-PC formatting shared by the page and fine-grained reports. */
#ifndef MADEIRA_PERF_GUEST_PC_H
#define MADEIRA_PERF_GUEST_PC_H
#include <stdint.h>
#include <stdio.h>
static inline void ios_perf_guest_pc_name(char *out, size_t size, const char *module,
                                         uint64_t rva, uint64_t rip, int fine)
{
    uint64_t mask = fine ? ~UINT64_C(15) : ~UINT64_C(4095);
    snprintf(out, size, "%s+0x%llx", module ? module : "guest",
             (unsigned long long)((module ? rva : rip) & mask));
}
#endif
