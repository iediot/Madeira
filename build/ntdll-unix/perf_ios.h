/* Bounded, owned snapshot: no pointers into unloadable PE mappings. */
#ifndef MADEIRA_PERF_IOS_H
#define MADEIRA_PERF_IOS_H
#include <stdint.h>
#include <stddef.h>
#define IOS_PERF_IMAGES 512
struct ios_perf_image
{
    uintptr_t base;
    size_t size;
    char name[64];
};
unsigned ios_perf_image_snapshot(struct ios_perf_image *out, unsigned cap);
#endif
