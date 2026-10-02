# Madeira's JIT protocol for LLDB, so Run from Xcode works the way a StikDebug
# launch does. A port of app/Madeira/madeira-jit.js: same commands, same answers.
#
# Madeira asks the debugger for executable memory with BRK #0xf00d, the command
# in x16 and its arguments in x0/x1 (app/Madeira/JITAllocator.c). Under
# StikDebug the script answers it; under Xcode's LLDB nothing did, so the BRK
# surfaced as EXC_BREAKPOINT in jit26_prepare_region and the app died.
#
# A scripted stop hook sees every stop. A Madeira BRK is answered and the
# process resumed; any other stop is left alone, so a real crash still stops in
# Xcode with its backtrace.
#
#   x16 = 0  CMD_DETACH          nothing to do: Xcode stays attached
#   x16 = 1  CMD_PREPARE_REGION  x0 = address (0: allocate x1 bytes RX), x1 = size;
#                                answer x0 = the region
#   x16 = 3  CMD_MAP_PAGE_ZERO   best effort, x0 = 1 on success
#   BRK #0x69 (legacy)           prepare x0 bytes at x0
#
# "Prepare" is what StikDebug's prepare_memory_region does (JSDebugSupport.swift,
# makeBulkWriteCommands): a one-byte debugger write of 0x69 at the start of every
# 16 KB page, which lets iOS map the page executable afterwards.
#
# Loaded by tools/lldb/madeira.lldbinit (the scheme's LLDB Init File).

import lldb

JIT_PAGE = 16384
BRK_MASK, BRK_BITS = 0xFFE0001F, 0xD4200000


import os
import time

# Also written to a file on the Mac: Xcode's console does not always show a
# script's output, and the file can be read while the app runs.
LOG_PATH = os.path.expanduser("~/Library/Logs/madeira-jit.log")


def _log(msg):
    line = "[madeira-jit] " + msg
    print(line)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(time.strftime("%H:%M:%S ") + line + "\n")
    except OSError:
        pass


def _log_backtrace(thread, limit=40):
    """The crashing thread's backtrace into the log, so a crash can be read on
    the Mac without copying it out of Xcode's console."""
    for i in range(min(thread.GetNumFrames(), limit)):
        f = thread.GetFrameAtIndex(i)
        mod = f.GetModule().GetFileSpec().GetFilename() or "?"
        name = f.GetFunctionName() or f.GetSymbol().GetName() or "?"
        off = f.GetPC() - f.GetSymbol().GetStartAddress().GetLoadAddress(thread.GetProcess().GetTarget()) \
            if f.GetSymbol().IsValid() else 0
        _log("    #%-2d 0x%x %s`%s + %d" % (i, f.GetPC(), mod, name, off))


class MadeiraJIT:
    def __init__(self, target, extra_args, internal_dict):
        self.target = target
        self.requests = 0

    def handle_stop(self, exe_ctx, stream):
        process = exe_ctx.GetProcess()
        handled = False
        _log("stop: " + ", ".join("tid %d reason %d" % (t.GetThreadID(), t.GetStopReason()) for t in process
                                  if t.GetStopReason() not in (lldb.eStopReasonNone, lldb.eStopReasonInvalid)))
        keep = False
        for thread in process:
            reason = thread.GetStopReason()
            if reason == lldb.eStopReasonException:
                if self._handle_brk(process, thread):
                    handled = True
                else:
                    _log("exception on tid %d at 0x%x: a crash, left for Xcode" %
                         (thread.GetThreadID(), thread.GetFrameAtIndex(0).GetPC()))
                    _log_backtrace(thread)
                    keep = True          # a real fault: let Xcode show it
            elif reason == lldb.eStopReasonSignal:
                # Only SIGTRAP stops (madeira.lldbinit passes the rest silently). With a
                # debugger attached Madeira installs no SIGTRAP handler, so a SIGTRAP is
                # a crash: an abort()/__builtin_trap in a library. Keep it, so Xcode shows
                # the backtrace; continuing delivers it (pass=true), as StikDebug would.
                signo = thread.GetStopReasonDataAtIndex(0)
                pc = thread.GetFrameAtIndex(0).GetPC()
                err = lldb.SBError()
                raw = process.ReadMemory(pc, 4, err)
                at_brk = err.Success() and len(raw) == 4 and \
                    (int.from_bytes(raw, "little") & BRK_MASK) == BRK_BITS
                if at_brk:
                    # An abort()/__builtin_trap: a crash. Keep it for Xcode.
                    _log("signal %d on tid %d at 0x%x (a trap instruction): a crash, left for Xcode" %
                         (signo, thread.GetThreadID(), pc))
                    _log_backtrace(thread)
                    keep = True
                else:
                    # Raised with kill/raise (Wine signals itself, e.g. for a guest
                    # breakpoint): pass it on, as madeira-jit.js does. Observed: a
                    # SIGTRAP at the same pc was passed and the app carried on.
                    _log("signal %d on tid %d at 0x%x (raised): passed to the app" %
                         (signo, thread.GetThreadID(), pc))
                    handled = True
            elif reason not in (lldb.eStopReasonNone, lldb.eStopReasonInvalid):
                keep = True              # breakpoints, steps: the user's own stops
        # True keeps the stop (Xcode shows it); False resumes the process.
        return keep or not handled

    def _handle_brk(self, process, thread):
        frame = thread.GetFrameAtIndex(0)
        pc = frame.GetPC()
        err = lldb.SBError()
        raw = process.ReadMemory(pc, 4, err)
        if not err.Success() or len(raw) != 4:
            return False
        insn = int.from_bytes(raw, "little")
        if (insn & BRK_MASK) != BRK_BITS:
            return False
        imm = (insn >> 5) & 0xFFFF
        frame_x0 = frame.FindRegister("x0")
        if imm not in (0xF00D, 0x69):
            # As madeira-jit.js: skip a BRK that is not a Madeira request and answer
            # x0 = 0, the failure value the app's own SIGTRAP fallback would give.
            _log("unknown BRK #0x%x at 0x%x on tid %d: skipped, x0 = 0" % (imm, pc, thread.GetThreadID()))
            frame_x0.SetValueFromCString("0", err)
            frame.FindRegister("pc").SetValueFromCString(str(pc + 4), err)
            return True

        regs = lambda name: frame.FindRegister(name)
        x0 = regs("x0").GetValueAsUnsigned()
        x1 = regs("x1").GetValueAsUnsigned()
        x16 = regs("x16").GetValueAsUnsigned()
        answer = x0

        if imm == 0x69:
            if x0:
                self._prepare(process, x0, x0)
        elif x16 == 0:
            _log("detach requested; staying attached under Xcode")
        elif x16 == 1:
            answer = self._prepare_region(process, x0, x1)
        elif x16 == 3:
            answer = self._map_page_zero(process, x0, x1)
        else:
            answer = 0

        regs("x0").SetValueFromCString(str(answer), err)
        regs("pc").SetValueFromCString(str(pc + 4), err)
        self.requests += 1
        return True

    def _prepare_region(self, process, addr, size):
        if addr == 0 and size:
            err = lldb.SBError()
            addr = process.AllocateMemory(size, lldb.ePermissionsReadable | lldb.ePermissionsExecutable, err)
            if not err.Success():
                _log("allocate 0x%x bytes failed: %s" % (size, err.GetCString()))
                return 0
        if addr and size:
            self._prepare(process, addr, size)
        return addr

    def _prepare(self, process, addr, size):
        # One debugger write per page, as StikDebug does, costs a USB round trip
        # each from the Mac: measured over 6 ms a page, four-plus minutes for the
        # pool. A write that spans many pages is still a debugger write to every
        # page it covers, so write CHUNK pages at a time instead. The region is a
        # fresh pool nothing has used yet, so zeros are harmless (StikDebug
        # overwrites the start of each page too). MADEIRA_JIT_LLDB_CHUNK=1 (in the
        # environment Xcode runs in) restores one byte per page.
        chunk = max(1, int(os.environ.get("MADEIRA_JIT_LLDB_CHUNK", "64")))
        pages = (size + JIT_PAGE - 1) // JIT_PAGE
        _log("preparing 0x%x+0x%x (%d pages, %d per write)..." % (addr, size, pages, chunk))
        err = lldb.SBError()
        failed = 0
        start = time.time()
        zeros = bytes(chunk * JIT_PAGE)
        i = 0
        while i < pages:
            n = min(chunk, pages - i)
            data = b"\x69" if chunk == 1 else zeros[:n * JIT_PAGE]
            process.WriteMemory(addr + i * JIT_PAGE, data, err)
            if not err.Success():
                failed += n
            if (i // 4096) != ((i + n) // 4096):
                _log("  %d/%d pages, %.1fs" % (i + n, pages, time.time() - start))
            i += n
        _log("prepared %d pages, %d failed, %.1fs" % (pages, failed, time.time() - start))

    def _map_page_zero(self, process, teb, size):
        # The JS script's attempt, unchanged: copy 256 bytes of the TEB to the
        # same offset in page 0. The kernel usually refuses; 0 tells the app so.
        if not teb or not size:
            return 0
        err = lldb.SBError()
        data = process.ReadMemory(teb, 0x100, err)
        if not err.Success():
            return 0
        process.WriteMemory(teb & 0x3FFF, data, err)
        return 1 if err.Success() else 0


def handle_now(debugger, command, result, internal_dict):
    """madeira-jit-now: answer the Madeira BRK the process is stopped at and continue.
    For a session that hit the BRK before the stop hook was installed."""
    process = debugger.GetSelectedTarget().GetProcess()
    hook = MadeiraJIT(debugger.GetSelectedTarget(), None, internal_dict)
    handled = any(hook._handle_brk(process, t) for t in process
                  if t.GetStopReason() == lldb.eStopReasonException)
    if handled:
        process.Continue()
        result.AppendMessage("[madeira-jit] answered the pending request; continuing")
    else:
        result.AppendMessage("[madeira-jit] not stopped at a Madeira BRK")


def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand("target stop-hook add -P madeira_jit.MadeiraJIT")
    debugger.HandleCommand("command script add -o -f madeira_jit.handle_now madeira-jit-now")
    _log("stop hook installed (BRK #0xf00d / #0x69); madeira-jit-now answers a pending one")
