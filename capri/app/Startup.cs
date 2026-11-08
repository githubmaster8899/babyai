using System;
using System.IO;
using Microsoft.Win32;

namespace CapriDesktop
{
    // Starting with Windows is her value in the user's Run key, the same entry Task Manager's Startup apps turns on and off.
    internal static class Startup
    {
        internal static string RunKey = "Software\\Microsoft\\Windows\\CurrentVersion\\Run";
        internal static string ApprovedKey = "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\StartupApproved\\Run";
        internal const string Name = "Capri";

        internal static bool On(string exe)
        {
            try
            {
                using (RegistryKey run = Registry.CurrentUser.OpenSubKey(RunKey))
                    if (run == null || !Starts(run.GetValue(Name) as string, exe)) return false;
                using (RegistryKey approved = Registry.CurrentUser.OpenSubKey(ApprovedKey))
                {
                    byte[] state = approved == null ? null : approved.GetValue(Name) as byte[];
                    return state == null || state.Length == 0 || (state[0] & 1) == 0;
                }
            }
            catch (UnauthorizedAccessException) { return false; }
            catch (System.Security.SecurityException) { return false; }
            catch (IOException) { return false; }
        }

        internal static bool Set(string exe, bool on)
        {
            try
            {
                using (RegistryKey run = Registry.CurrentUser.CreateSubKey(RunKey))
                {
                    if (on) run.SetValue(Name, "\"" + Path.GetFullPath(exe) + "\"");
                    else run.DeleteValue(Name, false);
                }
                using (RegistryKey approved = Registry.CurrentUser.OpenSubKey(ApprovedKey, true))
                    if (approved != null) approved.DeleteValue(Name, false);
                return true;
            }
            catch (UnauthorizedAccessException) { return false; }
            catch (System.Security.SecurityException) { return false; }
            catch (IOException) { return false; }
        }

        // Removing a Capri only takes the entry away when it starts that Capri, not another copy.
        internal static void Clear(string exe)
        {
            string command = null;
            try
            {
                using (RegistryKey run = Registry.CurrentUser.OpenSubKey(RunKey))
                    if (run != null) command = run.GetValue(Name) as string;
            }
            catch (UnauthorizedAccessException) { return; }
            catch (System.Security.SecurityException) { return; }
            catch (IOException) { return; }
            if (Starts(command, exe)) Set(exe, false);
        }

        internal static bool Starts(string command, string exe)
        {
            if (string.IsNullOrWhiteSpace(command)) return false;
            string path = command.Trim();
            if (path.StartsWith("\"", StringComparison.Ordinal))
            {
                int end = path.IndexOf('"', 1);
                path = end > 1 ? path.Substring(1, end - 1) : path.Trim('"');
            }
            try { return string.Equals(Path.GetFullPath(path), Path.GetFullPath(exe), StringComparison.OrdinalIgnoreCase); }
            catch (ArgumentException) { return false; }
            catch (NotSupportedException) { return false; }
            catch (IOException) { return false; }
        }
    }
}
