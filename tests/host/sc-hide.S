/* [sc-hide] Win64 stubs for SocialClubHelper.exe 2.4.0.302 (virtual_ios.c
 * ios_sch302_hide_*). check-sc-hide.py assembles this file, compares the bytes
 * with the production arrays and executes the visibility stub against fake CEF
 * structs. Everything sits in one section placed at RVA 0x1a4680 (the zero
 * .text tail past the [sc-rph] blobs), so every helper RVA below is a fixed
 * displacement from `base`. The 64-bit immediates 0x5ac0de5ac0de5ac0 are the
 * host's ios_sc_vis address, written over them when the image is patched:
 *   +0 want   +4 applied  +8 browsers  +12 passes
 *   +16 show calls  +20 hide calls  +24 pump runs  +28 OnPaint calls
 *
 * vis: the [sc-rph] renderer loop pump's final CALL [cef_post_delayed_task]
 * (task re-post, RCX=6 RDX=task R8=10) is redirected here. When want differs
 * from applied, browsers 1..32 are looked up with
 * cef_browser_host_get_browser_by_identifier; each found browser's host gets
 * was_hidden(want) (cef_browser_t.get_host at +0x30, cef_browser_host_t.was_hidden
 * at +0x138, both size-checked; both references released through base.release
 * at +0x10), then applied = want. CEF posts WasHidden to its UI thread itself.
 * The original call then runs as a tail jump with its three arguments intact. */
.intel_syntax noprefix
.text
.set RVA_BASE,      0x1a4680
.set RVA_GET_BY_ID, 0x1a5b28   /* IAT: cef_browser_host_get_browser_by_identifier */
.set RVA_POST,      0x1a5b10   /* IAT: cef_post_delayed_task */
.set RVA_SHOW,      0x2ca60    /* browser window Show(): 40 53 48 83 ec 30 */
.set RVA_HIDE,      0x2b0c0    /* browser window Hide(): 40 53 48 83 ec 30 */
.set RVA_PAINT,     0xa11e0    /* CefRenderHandlerCppToC on_paint: 40 53 55 41 56 */
.global base, vis, show_hook, hide_hook, paint_hook
base:
vis:
    push rcx
    push rdx
    push r8
    push rbx
    push rsi
    push rdi
    sub rsp, 0x38
    movabs rbx, 0x5ac0de5ac0de5ac0
    lock inc dword ptr [rbx+24]
    mov esi, dword ptr [rbx]
    cmp esi, dword ptr [rbx+4]
    je vis_done
    mov dword ptr [rbx+8], 0
    mov dword ptr [rsp+0x20], 1
vis_loop:
    mov ecx, dword ptr [rsp+0x20]
    call qword ptr [rip + base + (RVA_GET_BY_ID - RVA_BASE)]
    test rax, rax
    jz vis_next
    mov rdi, rax
    cmp qword ptr [rdi], 0x38
    jb vis_release_browser
    mov rcx, rdi
    call qword ptr [rdi+0x30]
    test rax, rax
    jz vis_release_browser
    mov qword ptr [rsp+0x28], rax
    cmp qword ptr [rax], 0x140
    jb vis_release_host
    mov rcx, rax
    mov edx, esi
    call qword ptr [rax+0x138]
    inc dword ptr [rbx+8]
vis_release_host:
    mov rcx, qword ptr [rsp+0x28]
    call qword ptr [rcx+0x10]
vis_release_browser:
    mov rcx, rdi
    call qword ptr [rdi+0x10]
vis_next:
    inc dword ptr [rsp+0x20]
    cmp dword ptr [rsp+0x20], 32
    jbe vis_loop
    mov dword ptr [rbx+4], esi
    lock inc dword ptr [rbx+12]
vis_done:
    add rsp, 0x38
    pop rdi
    pop rsi
    pop rbx
    pop r8
    pop rdx
    pop rcx
    jmp qword ptr [rip + base + (RVA_POST - RVA_BASE)]

.balign 16, 0xcc
show_hook:                      /* entered by JMP from Show()'s first 6 bytes */
    push rax
    movabs rax, 0x5ac0de5ac0de5ac0
    lock inc dword ptr [rax+16]
    pop rax
    push rbx
    sub rsp, 0x30
    jmp base + (RVA_SHOW + 6 - RVA_BASE)

.balign 16, 0xcc
hide_hook:                      /* entered by JMP from Hide()'s first 6 bytes */
    push rax
    movabs rax, 0x5ac0de5ac0de5ac0
    lock inc dword ptr [rax+20]
    pop rax
    push rbx
    sub rsp, 0x30
    jmp base + (RVA_HIDE + 6 - RVA_BASE)

.balign 16, 0xcc
paint_hook:                     /* entered by JMP from on_paint's first 5 bytes */
    push rax
    movabs rax, 0x5ac0de5ac0de5ac0
    lock inc dword ptr [rax+28]
    pop rax
    push rbx
    push rbp
    push r14
    jmp base + (RVA_PAINT + 5 - RVA_BASE)
