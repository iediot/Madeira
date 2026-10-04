# JIT setup

Madeira needs an attached debugger to create executable memory on iOS. This
fork uses the StikDebug app for that; upstream's Built-in StikJIT helper (an
app extension that needs its own App ID) was removed.

1. Install [StikDebug](https://github.com/StikDebug/StikDebug/releases/latest).
2. Import this iPhone's pairing file into StikDebug.
3. Install and connect
   [LocalDevVPN](https://apps.apple.com/us/app/localdevvpn/id6755608044).
4. In Madeira, tap **Enable JIT**, or turn on **Enable JIT automatically** in
   Settings so it runs once at every launch.

Madeira opens StikDebug's canonical `stikdebug://enable-jit` URL with Madeira's
bundle identifier, current process ID, and the bundled `madeira-jit.js` script.
It waits up to 90 seconds for both the `CS_DEBUGGED` flag and a live debugger
connection. Enabling Madeira from StikDebug's own app list is not equivalent
because that flow can detach before Madeira creates its JIT pool.

When running from Xcode, the shared scheme launches without the debugger;
`tools/lldb/madeira.lldbinit` answers the JIT script's requests if you attach
LLDB instead.
