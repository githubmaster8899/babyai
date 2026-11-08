using System;
using System.Runtime.InteropServices;

namespace CapriDesktop
{
    internal static class Native
    {
        [DllImport("user32.dll")] internal static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] internal static extern IntPtr GetWindow(IntPtr window, int command);
        [DllImport("user32.dll")] internal static extern int GetWindowLong(IntPtr window, int index);
        [DllImport("user32.dll")] internal static extern void keybd_event(byte key, byte scan, int flags, UIntPtr extra);
    }
}
