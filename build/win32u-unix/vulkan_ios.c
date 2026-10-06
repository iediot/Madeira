/* Wine's Vulkan (win32u/vulkan.c) unchanged, loading the MoltenVK the app
 * already ships for OpenGL (app/Madeira/gl/libMoltenVK.dylib, MADEIRA_GL_DIR)
 * when it asks for SONAME_LIBVULKAN. From PR #88 (xssp11), which embedded a
 * second MoltenVK as a framework instead.
 * SPDX-License-Identifier: GPL-3.0-or-later
 * With the additional permission in LICENSE-EXCEPTION.md.
 */
#include <dlfcn.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void *madeira_vulkan_dlopen( const char *path, int flags )
{
    const char *dir = getenv( "MADEIRA_GL_DIR" );
    char absolute[PATH_MAX];
    int length;

    if (strchr( path, '/' ) || !dir || !*dir) return dlopen( path, flags );
    length = snprintf( absolute, sizeof(absolute), "%s/%s", dir, path );
    if (length < 0 || (size_t)length >= sizeof(absolute)) return NULL;
    return dlopen( absolute, flags );
}

#define dlopen madeira_vulkan_dlopen
#include "../../wine/dlls/win32u/vulkan.c"
#undef dlopen
