/* Regression: an unset c16 must read zero after switching from a shader that
 * only reads c0, without changing any constants between the draws.
 * The old packed upload put i0 exactly where an out-of-range c16 read landed.
 * Run on a D3D9 device; exit 0 passes, 1 fails. */
#define COBJMACROS
#include <windows.h>
#include <d3d9.h>
#include <stdio.h>

#define CHECK(call) do { HRESULT hr = (call); if (FAILED(hr)) { \
    fprintf(stderr, "%s failed: %08lx\n", #call, (unsigned long)hr); return 1; } } while (0)

int main(void) {
    HWND window = CreateWindowA("STATIC", "D3D9 unset constants", WS_OVERLAPPEDWINDOW,
        0, 0, 64, 64, NULL, NULL, GetModuleHandleA(NULL), NULL);
    IDirect3D9 *d3d = Direct3DCreate9(D3D_SDK_VERSION);
    IDirect3DDevice9 *device;
    IDirect3DPixelShader9 *low, *high;
    IDirect3DSurface9 *rt, *readback;
    D3DPRESENT_PARAMETERS pp = {0};
    /* ps_2_0: mov oC0, c0 / c16 */
    const DWORD low_code[] = {0xffff0200, 0x02000001, 0x800f0800, 0xa0e40000, 0x0000ffff};
    const DWORD high_code[] = {0xffff0200, 0x02000001, 0x800f0800, 0xa0e40010, 0x0000ffff};
    const float green[4] = {0, 1, 0, 1};
    const int poison[4] = {0x3f800000, 0, 0, 0x3f800000};
    float quad[][4] = {{0,0,0,1}, {10,0,0,1}, {0,32,0,1}, {10,32,0,1}};
    if (!window || !d3d) return 1;
    pp.Windowed = TRUE;
    pp.SwapEffect = D3DSWAPEFFECT_DISCARD;
    pp.BackBufferWidth = pp.BackBufferHeight = 32;
    pp.BackBufferFormat = D3DFMT_X8R8G8B8;
    CHECK(IDirect3D9_CreateDevice(d3d, 0, D3DDEVTYPE_HAL, window,
        D3DCREATE_SOFTWARE_VERTEXPROCESSING, &pp, &device));
    CHECK(IDirect3DDevice9_CreatePixelShader(device, low_code, &low));
    CHECK(IDirect3DDevice9_CreatePixelShader(device, high_code, &high));
    CHECK(IDirect3DDevice9_GetRenderTarget(device, 0, &rt));
    CHECK(IDirect3DDevice9_CreateOffscreenPlainSurface(device, 32, 32,
        D3DFMT_X8R8G8B8, D3DPOOL_SYSTEMMEM, &readback, NULL));
    CHECK(IDirect3DDevice9_SetFVF(device, D3DFVF_XYZRHW));
    CHECK(IDirect3DDevice9_SetRenderState(device, D3DRS_CULLMODE, D3DCULL_NONE));
    CHECK(IDirect3DDevice9_SetRenderState(device, D3DRS_ZENABLE, FALSE));
    CHECK(IDirect3DDevice9_SetPixelShaderConstantF(device, 0, green, 1));
    CHECK(IDirect3DDevice9_SetPixelShaderConstantI(device, 0, poison, 1));
    /* Keep all three draws in one batch: green / unset black / green. */
    CHECK(IDirect3DDevice9_BeginScene(device));
    for (unsigned pass = 0; pass < 3; ++pass) {
        quad[0][0] = quad[2][0] = (float)(pass * 10);
        quad[1][0] = quad[3][0] = (float)(pass * 10 + 10);
        CHECK(IDirect3DDevice9_SetPixelShader(device, pass == 1 ? high : low));
        CHECK(IDirect3DDevice9_DrawPrimitiveUP(device, D3DPT_TRIANGLESTRIP, 2, quad, sizeof(quad[0])));
    }
    CHECK(IDirect3DDevice9_EndScene(device));
    CHECK(IDirect3DDevice9_GetRenderTargetData(device, rt, readback));
    D3DLOCKED_RECT locked;
    CHECK(IDirect3DSurface9_LockRect(readback, &locked, NULL, D3DLOCK_READONLY));
    for (unsigned pass = 0; pass < 3; ++pass) {
        DWORD color = *(DWORD *)((BYTE *)locked.pBits + 16 * locked.Pitch + (5 + pass * 10) * 4) & 0xffffff;
        if (color != (pass == 1 ? 0 : 0x00ff00)) {
            fprintf(stderr, "pass %u: RGB %06lx\n", pass, (unsigned long)color);
            return 1;
        }
    }
    CHECK(IDirect3DSurface9_UnlockRect(readback));
    IDirect3DSurface9_Release(readback);
    IDirect3DSurface9_Release(rt);
    IDirect3DPixelShader9_Release(high);
    IDirect3DPixelShader9_Release(low);
    IDirect3DDevice9_Release(device);
    IDirect3D9_Release(d3d);
    DestroyWindow(window);
    puts("unset constant regression passed");
    return 0;
}
