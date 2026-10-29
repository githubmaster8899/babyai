using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text.RegularExpressions;
using System.Threading;

namespace CapriDesktop
{
    internal static class Limbs
    {
        internal static Action<string, string[]> Launch = StartTool;
        internal static int Patience = 20;
        internal static int Settle = 1500;
        private static readonly bool OnMac = OperatingSystem.IsMacOS();
        private static readonly Regex Field = new Regex(@"\s*%[fFuUdDnNickvm]");
        private static readonly string[] MacPlayers = { "Spotify", "Music" };

        private static string soundPlayer;
        private static Process speaker;
        private static int speeches;

        internal static void Sound(short[] samples)
        {
            ThreadPool.QueueUserWorkItem(delegate
            {
                string path = Path.Combine(Path.GetTempPath(), "capri-sound-" + Guid.NewGuid().ToString("N") + ".wav");
                try
                {
                    File.WriteAllBytes(path, Voice.Wave(samples, Sfx.Rate));
                    Music.Run(Player(), new[] { path });
                }
                catch (IOException) { }
                finally
                {
                    try { File.Delete(path); }
                    catch (IOException) { }
                }
            });
        }

        private static string Player()
        {
            if (soundPlayer == null) soundPlayer = OnMac ? "afplay" : Music.Run("sh", new[] { "-c", "command -v paplay" }) != null ? "paplay" : "aplay";
            return soundPlayer;
        }

        internal static void Speak(byte[] wav)
        {
            Hush();
            int turn = Interlocked.Increment(ref speeches);
            ThreadPool.QueueUserWorkItem(delegate
            {
                string path = Path.Combine(Path.GetTempPath(), "capri-voice-" + Guid.NewGuid().ToString("N") + ".wav");
                try
                {
                    File.WriteAllBytes(path, wav);
                    ProcessStartInfo start = new ProcessStartInfo(Player()) { UseShellExecute = false, CreateNoWindow = true };
                    start.ArgumentList.Add(path);
                    using (Process process = Process.Start(start))
                    {
                        if (process == null) return;
                        speaker = process;
                        if (turn != speeches) Hush();
                        process.WaitForExit(120000);
                        if (speaker == process) speaker = null;
                    }
                }
                catch (Exception) { }
                finally
                {
                    try { File.Delete(path); }
                    catch (IOException) { }
                }
            });
        }

        internal static void Hush()
        {
            Interlocked.Increment(ref speeches);
            Process playing = speaker;
            speaker = null;
            try { if (playing != null && !playing.HasExited) playing.Kill(); }
            catch (Exception) { }
        }

        internal static void Attach(Brain brain)
        {
            Hands.Run = delegate(string id) { Open(id, OnMac); };
            Hands.Press = delegate(byte key) { Key(key, OnMac); };
            Hands.Catalog = delegate { return OnMac ? MacApps(MacFolders()) : LinuxApps(LinuxFolders()); };
            Hands.NowPlaying = delegate { return Song(OnMac); };
            Hands.Windows = delegate { return new List<OpenWindow>(); };
            Hands.Chord = delegate { throw new PlatformNotSupportedException("keyboard shortcuts only work on Windows for now"); };
            brain.Act = Hands.Do;
        }

        internal static void Open(string id, bool mac)
        {
            if (id.StartsWith("exec:", StringComparison.Ordinal)) { Launch("/bin/sh", new[] { "-c", id.Substring(5) }); return; }
            if (Regex.IsMatch(id, @"^spotify:(track|album|playlist|artist):") && PlaySpotify(id, mac)) return;
            Launch(mac ? "open" : "xdg-open", new[] { id });
        }

        internal static bool PlaySpotify(string uri, bool mac)
        {
            if (mac)
            {
                if (!Running("Spotify"))
                {
                    Launch("open", new[] { "-a", "Spotify" });
                    if (!Wait(delegate { return Running("Spotify"); })) return false;
                    Thread.Sleep(Settle);
                }
                return Music.Run("osascript", new[] { "-e", "tell application \"Spotify\" to play track \"" + uri + "\"" }) != null;
            }
            if (OpenUri(uri)) return true;
            Launch("xdg-open", new[] { uri });
            Wait(delegate { return OpenUri(uri); });
            return true;
        }

        private static bool OpenUri(string uri)
        {
            return Music.Run("dbus-send", new[] { "--session", "--print-reply", "--dest=org.mpris.MediaPlayer2.spotify", "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player.OpenUri", "string:" + uri }) != null;
        }

        private static bool Running(string app) { return Music.Run("pgrep", new[] { "-x", app }) != null; }

        private static bool Wait(Func<bool> done)
        {
            for (int i = 0; i < Patience; i++)
            {
                if (done()) return true;
                Thread.Sleep(500);
            }
            return false;
        }

        internal static void Key(byte key, bool mac)
        {
            string command = key == 0xB3 ? "playpause" : key == 0xB0 ? "next track" : key == 0xB1 ? "previous track" : null;
            string method = key == 0xB3 ? "PlayPause" : key == 0xB0 ? "Next" : key == 0xB1 ? "Previous" : null;
            if (mac)
            {
                if (command != null)
                {
                    foreach (string app in MacPlayers)
                        if (Running(app)) { Music.Run("osascript", new[] { "-e", "tell application \"" + app + "\" to " + command }); return; }
                    return;
                }
                string volume = key == 0xAF ? "set volume output volume ((output volume of (get volume settings)) + 2)" : key == 0xAE ? "set volume output volume ((output volume of (get volume settings)) - 2)" : key == 0xAD ? "set volume output muted (not (output muted of (get volume settings)))" : null;
                if (volume != null) Music.Run("osascript", new[] { "-e", volume });
                return;
            }
            if (method != null)
            {
                string player = Music.Player();
                if (player != null) Music.Run("dbus-send", new[] { "--session", "--print-reply", "--dest=" + player, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player." + method });
                return;
            }
            string[] pactl = key == 0xAF ? new[] { "set-sink-volume", "@DEFAULT_SINK@", "+2%" } : key == 0xAE ? new[] { "set-sink-volume", "@DEFAULT_SINK@", "-2%" } : key == 0xAD ? new[] { "set-sink-mute", "@DEFAULT_SINK@", "toggle" } : null;
            if (pactl != null && Music.Run("pactl", pactl) == null) Music.Run("amixer", new[] { "-q", "sset", "Master", key == 0xAF ? "2%+" : key == 0xAE ? "2%-" : "toggle" });
        }

        internal static string Song(bool mac)
        {
            if (mac)
            {
                if (!Running("Spotify")) return "";
                string playing = Music.Run("osascript", new[] { "-e", "tell application \"Spotify\" to if player state is playing then return (artist of current track) & \" - \" & (name of current track)" });
                return playing == null ? "" : playing.Trim();
            }
            string status = Music.Run("dbus-send", new[] { "--session", "--print-reply", "--dest=org.mpris.MediaPlayer2.spotify", "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties.Get", "string:org.mpris.MediaPlayer2.Player", "string:PlaybackStatus" });
            if (status == null || !status.Contains("\"Playing\"")) return "";
            string data = Music.Run("dbus-send", new[] { "--session", "--print-reply", "--dest=org.mpris.MediaPlayer2.spotify", "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties.Get", "string:org.mpris.MediaPlayer2.Player", "string:Metadata" });
            if (data == null) return "";
            Match artist = Regex.Match(data, "\"xesam:artist\"\\s*variant\\s*array \\[\\s*string \"([^\"]*)\"");
            Match title = Regex.Match(data, "\"xesam:title\"\\s*variant\\s*string \"([^\"]*)\"");
            if (!title.Success) return "";
            return (artist.Success ? artist.Groups[1].Value + " - " : "") + title.Groups[1].Value;
        }

        internal static string[] MacFolders()
        {
            string home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            return new[] { "/Applications", "/Applications/Utilities", "/System/Applications", "/System/Applications/Utilities", Path.Combine(home, "Applications") };
        }

        internal static List<InstalledApp> MacApps(IEnumerable<string> folders)
        {
            List<InstalledApp> apps = new List<InstalledApp>();
            foreach (string folder in folders)
            {
                if (!Directory.Exists(folder)) continue;
                try { foreach (string app in Directory.GetDirectories(folder, "*.app")) apps.Add(new InstalledApp(Path.GetFileNameWithoutExtension(app), app)); }
                catch (Exception) { }
            }
            return apps;
        }

        internal static string[] LinuxFolders()
        {
            string home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            string data = Environment.GetEnvironmentVariable("XDG_DATA_HOME");
            List<string> folders = new List<string> { Path.Combine(string.IsNullOrEmpty(data) ? Path.Combine(home, ".local", "share") : data, "applications") };
            string dirs = Environment.GetEnvironmentVariable("XDG_DATA_DIRS");
            foreach (string dir in (string.IsNullOrEmpty(dirs) ? "/usr/local/share:/usr/share" : dirs).Split(':'))
                if (dir.Length > 0) folders.Add(Path.Combine(dir, "applications"));
            folders.Add("/var/lib/flatpak/exports/share/applications");
            folders.Add(Path.Combine(home, ".local", "share", "flatpak", "exports", "share", "applications"));
            folders.Add("/var/lib/snapd/desktop/applications");
            return folders.ToArray();
        }

        internal static List<InstalledApp> LinuxApps(IEnumerable<string> folders)
        {
            List<InstalledApp> apps = new List<InstalledApp>();
            HashSet<string> seen = new HashSet<string>();
            foreach (string folder in folders)
            {
                if (!Directory.Exists(folder)) continue;
                string[] files;
                try { files = Directory.GetFiles(folder, "*.desktop", SearchOption.AllDirectories); }
                catch (Exception) { continue; }
                foreach (string file in files)
                {
                    if (!seen.Add(Path.GetFileName(file))) continue;
                    try
                    {
                        InstalledApp app = Desktop(File.ReadAllLines(file));
                        if (app != null) apps.Add(app);
                    }
                    catch (Exception) { }
                }
            }
            return apps;
        }

        internal static InstalledApp Desktop(string[] lines)
        {
            bool entry = false;
            bool hidden = false;
            bool application = false;
            string name = null;
            string exec = null;
            foreach (string raw in lines)
            {
                string line = raw.Trim();
                if (line.StartsWith("[", StringComparison.Ordinal)) { entry = line == "[Desktop Entry]"; continue; }
                if (!entry) continue;
                if (name == null && line.StartsWith("Name=", StringComparison.Ordinal)) name = line.Substring(5).Trim();
                else if (exec == null && line.StartsWith("Exec=", StringComparison.Ordinal)) exec = line.Substring(5).Trim();
                else if (line == "NoDisplay=true" || line == "Hidden=true") hidden = true;
                else if (line == "Type=Application") application = true;
            }
            if (hidden || !application || string.IsNullOrEmpty(name) || string.IsNullOrEmpty(exec)) return null;
            return new InstalledApp(name, "exec:" + Field.Replace(exec, "").Replace("%%", "%").Trim());
        }

        private static void StartTool(string file, string[] args)
        {
            try
            {
                ProcessStartInfo start = new ProcessStartInfo(file) { UseShellExecute = false, CreateNoWindow = true };
                foreach (string arg in args) start.ArgumentList.Add(arg);
                using (Process.Start(start)) { }
            }
            catch (Exception) { }
        }
    }
}
