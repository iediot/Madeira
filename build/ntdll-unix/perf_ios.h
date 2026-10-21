/* Bounded, owned snapshot: no pointers into unloadable PE mappings. */
#ifndef MADEIRA_PERF_IOS_H
#define MADEIRA_PERF_IOS_H
#include <stdint.h>
#include <stddef.h>
#define IOS_PERF_IMAGES 512
#define IOS_PERF_FEX_RANGES 512
struct ios_perf_image
{
    uintptr_t base;
    size_t size;
    char name[64];
};
struct ios_perf_fex_range
{
    uintptr_t base;
    size_t size;
};
unsigned ios_perf_image_snapshot(struct ios_perf_image *out, unsigned cap);
unsigned ios_perf_fex_range_snapshot(struct ios_perf_fex_range *out, unsigned cap);
void ios_perf_fex_range_publish(const char *name, const void *base, size_t size);
#endif
