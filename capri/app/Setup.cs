using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Threading;
using System.Windows.Forms;
using CapriDesktop;
using Microsoft.Win32;

namespace CapriSetup
{
    internal static class Program
    {
        internal static string InstallPath { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Capri"); } }
        internal const string RegistryPath = "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Capri.Desktop";

        [STAThread]
        private static int Main(string[] args)
        {
            try { SetProcessDpiAwarenessContext(new IntPtr(-4)); } catch (EntryPointNotFoundException) { }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            if (args.Length > 0 && args[0] == "--update") return Update(args);
            if (args.Length == 2 && args[0] == "--verify") return Verify(args[1]);
            if (args.Length == 2 && args[0] == "--verify-voice") return VerifyVoice(args[1]);
            if (args.Length == 1 && args[0] == "--remove-files") return RemoveFiles();
            using (SetupWindow window = new SetupWindow(args.Length == 1 && args[0] == "--uninstall")) Application.Run(window);
            return 0;
        }

        [DllImport("user32.dll")] private static extern bool SetProcessDpiAwarenessContext(IntPtr context);

        private static int Update(string[] args)
        {
            try
            {
                int parent;
                long started;
                if (args.Length != 3 || !int.TryParse(args[1], System.Globalization.NumberStyles.None, System.Globalization.CultureInfo.InvariantCulture, out parent) || parent <= 0 || !long.TryParse(args[2], System.Globalization.NumberStyles.None, System.Globalization.CultureInfo.InvariantCulture, out started) || started <= 0) throw new IOException("The update command is invalid.");
                Process process = null;
                try { process = Process.GetProcessById(parent); } catch (ArgumentException) { }
                if (process != null)
                    using (process)
                    {
                        bool same = false;
                        try { same = process.StartTime.ToUniversalTime().Ticks == started; } catch (InvalidOperationException) { }
                        if (same && !process.WaitForExit(60000)) throw new IOException("Capri is still closing. Try the update again.");
                    }
                Install(false);
                using (Process launched = Process.Start(new ProcessStartInfo(Path.Combine(InstallPath, "Capri.exe")) { UseShellExecute = true, WorkingDirectory = InstallPath }))
                    if (launched == null) throw new IOException("Capri updated but couldn't restart. Open her from the Start menu.");
                return 0;
            }
            catch (Exception error) { MessageBox.Show(error.Message, "Capri couldn't update", MessageBoxButtons.OK, MessageBoxIcon.Error); return 1; }
        }

        internal static byte[] Payload()
        {
            using (Stream source = Assembly.GetExecutingAssembly().GetManifestResourceStream("Capri.App.exe"))
            using (MemoryStream memory = new MemoryStream())
            {
                if (source == null) throw new InvalidDataException("The Capri application payload is missing.");
                source.CopyTo(memory);
                return memory.ToArray();
            }
        }

        private static int Verify(string output)
        {
            try
            {
                output = Path.GetFullPath(output);
                Directory.CreateDirectory(output);
                byte[] bytes = Payload();
                if (bytes.Length < 1024 || bytes[0] != 77 || bytes[1] != 90) throw new InvalidDataException("Application payload is not a Windows executable.");
                File.WriteAllBytes(Path.Combine(output, "Capri.exe"), bytes);
                using (SHA256 sha = SHA256.Create()) File.WriteAllText(Path.Combine(output, "payload.sha256"), BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant());
                if (!Ours(Path.Combine(output, "Capri.exe"))) throw new InvalidDataException("The installer doesn't recognise its own Capri.exe.");
                if (Ours(Application.ExecutablePath) || Ours(Path.Combine(Environment.SystemDirectory, "notepad.exe")) || Ours(Path.Combine(output, "missing.exe"))) throw new InvalidDataException("The installer takes other programs for Capri.");
                string home = Path.Combine(output, "Capri");
                if (!Inside(Path.Combine(home, "Capri.exe"), home) || !Inside(Path.Combine(home, "brain", "llama-server.exe"), home + "\\") || Inside(Path.Combine(output, "Capri2", "Capri.exe"), home) || Inside(Path.Combine(Environment.SystemDirectory, "notepad.exe"), home) || Inside(home, home)) throw new InvalidDataException("The uninstaller would close the wrong programs.");
                using (SetupWindow preview = new SetupWindow(false))
                {
                    preview.Show();
                    Application.DoEvents();
                    Snap(preview, Path.Combine(output, "installer.png"));
                    preview.Speaking(127L << 20, VoicePack.Size, Megabytes(127L << 20) + " of " + Megabytes(VoicePack.Size));
                    Application.DoEvents();
                    if (!preview.Saying("Installing the local speech model", "This may take some time")) throw new InvalidDataException("The installer doesn't say it's installing the local speech model.");
                    Snap(preview, Path.Combine(output, "installer-speech.png"));
                    preview.Close();
                }
                File.WriteAllText(Path.Combine(output, "packaging-test.txt"), "PASS embedded executable extracted\nPASS PE signature\nPASS SHA-256 computed\nPASS an unmarked Capri.exe is recognised as Capri, other programs aren't\nPASS installer window rendered\nPASS the installer says it's installing the local speech model, that it may take some time, and that an API key gives a better, faster voice\nPASS the uninstaller closes Capri and anything running from her folder, like her brain, and nothing else\nLIMIT installation and uninstallation were not performed\n");
                return 0;
            }
            catch (Exception error) { Directory.CreateDirectory(output); File.WriteAllText(Path.Combine(output, "failure.txt"), error.ToString()); return 1; }
        }

        internal static void Install(bool desktop)
        {
            string destination = InstallPath;
            string marker = Path.Combine(destination, ".capri-install");
            string existing = Path.Combine(destination, "Capri.exe");
            if (Directory.Exists(destination) && !File.Exists(marker) && File.Exists(existing) && !Ours(existing)) throw new IOException("There's a Capri.exe in " + destination + " that isn't this Capri. Move it out of that folder, then install again.");
            foreach (Process process in Process.GetProcessesByName("Capri"))
            {
                using (process)
                {
                    try { if (string.Equals(process.MainModule.FileName, Path.Combine(destination, "Capri.exe"), StringComparison.OrdinalIgnoreCase)) throw new IOException("Quit Capri from her tray menu, then install again."); }
                    catch (System.ComponentModel.Win32Exception) { }
                }
            }
            Directory.CreateDirectory(destination);
            string version = Assembly.GetExecutingAssembly().GetName().Version.ToString(3);
            File.WriteAllText(marker, "Capri desktop companion " + version);
            string target = Path.Combine(destination, "Capri.exe");
            string temporary = target + ".new";
            try
            {
                File.WriteAllBytes(temporary, Payload());
                if (File.Exists(target)) File.Replace(temporary, target, null); else File.Move(temporary, target);
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
            File.Copy(Application.ExecutablePath, Path.Combine(destination, "Uninstall.exe"), true);
            string menu = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "Capri.lnk");
            Shortcut(menu, Path.Combine(destination, "Capri.exe"));
            if (desktop) Shortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Capri.lnk"), Path.Combine(destination, "Capri.exe"));
            using (RegistryKey key = Registry.CurrentUser.CreateSubKey(RegistryPath))
            {
                key.SetValue("DisplayName", "Capri");
                key.SetValue("DisplayVersion", version);
                key.SetValue("Publisher", "Capri fan project");
                key.SetValue("InstallLocation", destination);
                key.SetValue("DisplayIcon", Path.Combine(destination, "Capri.exe"));
                key.SetValue("UninstallString", "\"" + Path.Combine(destination, "Uninstall.exe") + "\" --uninstall");
                key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                key.SetValue("EstimatedSize", (Payload().Length + new FileInfo(Application.ExecutablePath).Length) / 1024, RegistryValueKind.DWord);
            }
        }

        private static void Snap(Form form, string path)
        {
            using (Bitmap bitmap = new Bitmap(form.Width, form.Height))
            {
                form.DrawToBitmap(bitmap, new Rectangle(Point.Empty, bitmap.Size));
                bitmap.Save(path, System.Drawing.Imaging.ImageFormat.Png);
            }
        }

        // Fetches the real voice pack into a test folder, the same way an install does.
        private static int VerifyVoice(string data)
        {
            try
            {
                data = Path.GetFullPath(data);
                Directory.CreateDirectory(data);
                FetchVoice(data, delegate { }, delegate { return false; });
                if (!VoicePack.Complete(VoicePack.Folder(data))) throw new IOException("The local speech model is missing files after installing.");
                File.WriteAllText(Path.Combine(data, "voice-test.txt"), "PASS the local speech model downloaded, matched its SHA-256 and unpacked where Capri looks for it\n");
                return 0;
            }
            catch (Exception error) { Directory.CreateDirectory(data); File.WriteAllText(Path.Combine(data, "failure.txt"), error.ToString()); return 1; }
        }

        internal static string Megabytes(long bytes) { return (bytes >> 20) + " MB"; }

        // Her local speech model goes where Capri's own download puts it, under the same file names, so either can finish
        // a download the other started.
        internal static void FetchVoice(string data, Action<string, long, long> progress, Func<bool> stopped)
        {
            string folder = VoicePack.Folder(data);
            if (VoicePack.Complete(folder)) return;
            string root = Path.GetDirectoryName(folder);
            Directory.CreateDirectory(root);
            long needed = VoicePack.Size * 3 + (64L << 20);
            string drive = Path.GetPathRoot(root);
            if (new DriveInfo(drive).AvailableFreeSpace < needed) throw new IOException("The local speech model needs " + Megabytes(needed) + " free on " + drive.TrimEnd('\\') + ".");
            string zip = folder + ".zip";
            string part = zip + ".part";
            ServicePointManager.SecurityProtocol |= (SecurityProtocolType)3072;
            long have = File.Exists(part) ? new FileInfo(part).Length : 0;
            if (have > VoicePack.Size) { File.Delete(part); have = 0; }
            int stalls = 0;
            while (have < VoicePack.Size)
            {
                long before = have;
                try
                {
                    HttpWebRequest request = (HttpWebRequest)WebRequest.Create(VoicePack.Url);
                    request.UserAgent = "Capri-Setup";
                    request.Timeout = 30000;
                    request.ReadWriteTimeout = 60000;
                    if (have > 0) request.AddRange(have);
                    using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                    {
                        if (have > 0 && response.StatusCode != HttpStatusCode.PartialContent) have = 0;
                        using (Stream body = response.GetResponseStream())
                        using (FileStream file = new FileStream(part, have > 0 ? FileMode.Append : FileMode.Create, FileAccess.Write))
                        {
                            byte[] buffer = new byte[1 << 16];
                            long shown = 0;
                            int read;
                            while ((read = body.Read(buffer, 0, buffer.Length)) > 0)
                            {
                                if (stopped()) throw new OperationCanceledException();
                                file.Write(buffer, 0, read);
                                have += read;
                                if (have - shown >= (1L << 20)) { shown = have; progress("Downloading", have, VoicePack.Size); }
                            }
                        }
                    }
                }
                catch (WebException) { if (stopped()) throw new OperationCanceledException(); }
                catch (IOException) { if (stopped()) throw new OperationCanceledException(); }
                if (have < VoicePack.Size)
                {
                    stalls = have > before ? 0 : stalls + 1;
                    if (stalls >= 4) throw new IOException("The download keeps stopping. Check your connection.");
                    Thread.Sleep(1500 * stalls);
                }
            }
            progress("Checking", VoicePack.Size, VoicePack.Size);
            string sum;
            using (SHA256 sha = SHA256.Create())
            using (FileStream file = File.OpenRead(part)) sum = BitConverter.ToString(sha.ComputeHash(file)).Replace("-", "").ToLowerInvariant();
            if (have != VoicePack.Size || sum != VoicePack.Sha) { File.Delete(part); throw new IOException("The download came down damaged. Try again from Capri Mainframe."); }
            if (File.Exists(zip)) File.Delete(zip);
            File.Move(part, zip);
            progress("Unpacking", VoicePack.Size, VoicePack.Size);
            string staging = folder + ".new";
            if (Directory.Exists(staging)) Directory.Delete(staging, true);
            ZipFile.ExtractToDirectory(zip, staging);
            if (!VoicePack.Complete(staging)) throw new IOException("The download is missing files.");
            if (Directory.Exists(folder)) Directory.Delete(folder, true);
            Directory.Move(staging, folder);
            File.Delete(zip);
        }

        internal static bool Ours(string path)
        {
            try
            {
                FileVersionInfo info = FileVersionInfo.GetVersionInfo(path);
                return info.ProductName == "Capri" && string.Equals(info.OriginalFilename, "Capri.exe", StringComparison.OrdinalIgnoreCase);
            }
            catch (FileNotFoundException) { return false; }
        }

        private static void Shortcut(string path, string target)
        {
            Type type = Type.GetTypeFromProgID("WScript.Shell");
            dynamic shell = Activator.CreateInstance(type);
            try
            {
                dynamic shortcut = shell.CreateShortcut(path);
                try { shortcut.TargetPath = target; shortcut.WorkingDirectory = Path.GetDirectoryName(target); shortcut.Description = "Capri desktop companion"; shortcut.IconLocation = target + ",0"; shortcut.Save(); }
                finally { Marshal.FinalReleaseComObject(shortcut); }
            }
            finally { Marshal.FinalReleaseComObject(shell); }
        }

        internal static void BeginUninstall()
        {
            string temporary = Path.Combine(Path.GetTempPath(), "Capri-Uninstall-" + Guid.NewGuid().ToString("N") + ".exe");
            File.Copy(Application.ExecutablePath, temporary);
            Process.Start(new ProcessStartInfo(temporary, "--remove-files") { UseShellExecute = true });
        }

        private static int RemoveFiles()
        {
            try
            {
                string destination = Path.GetFullPath(InstallPath);
                string expected = Path.GetFullPath(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Capri"));
                if (!string.Equals(destination, expected, StringComparison.OrdinalIgnoreCase) || !File.Exists(Path.Combine(destination, ".capri-install"))) throw new IOException("The Capri install marker could not be verified.");
                CloseEverything(destination);
                string uninstaller = Path.Combine(destination, "Uninstall.exe");
                for (int i = 0; i < 25; i++)
                {
                    try { File.Delete(uninstaller); break; }
                    catch (IOException) { System.Threading.Thread.Sleep(100); }
                }
                if (File.Exists(uninstaller)) throw new IOException("The uninstaller is still open. Close it and try removing Capri again.");
                Patiently(delegate { File.Delete(Path.Combine(destination, "Capri.exe")); });
                File.Delete(Path.Combine(destination, ".capri-install"));
                foreach (string pack in new[] { "brain", "voice", "ears" })
                {
                    string folder = Path.Combine(destination, pack);
                    Patiently(delegate { if (Directory.Exists(folder)) Directory.Delete(folder, true); });
                }
                DeleteShortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "Capri.lnk"), destination);
                DeleteShortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Capri.lnk"), destination);
                Startup.Clear(Path.Combine(destination, "Capri.exe"));
                Registry.CurrentUser.DeleteSubKeyTree(RegistryPath, false);
                MessageBox.Show("Capri has been removed.\n\nYour position and size settings were kept in " + destination + ".", "Capri", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return 0;
            }
            catch (Exception error) { MessageBox.Show(error.Message, "Capri could not be removed", MessageBoxButtons.OK, MessageBoxIcon.Error); return 1; }
        }

        // Capri and anything running from her folder, like her brain's engine, hold its files, so they're closed first.
        // Nothing outside the folder is touched.
        internal static void CloseEverything(string folder)
        {
            int self;
            using (Process me = Process.GetCurrentProcess()) self = me.Id;
            foreach (Process process in Process.GetProcesses())
            {
                using (process)
                {
                    try
                    {
                        if (process.Id == self || !Inside(process.MainModule.FileName, folder)) continue;
                        process.Kill();
                        process.WaitForExit(5000);
                    }
                    catch (System.ComponentModel.Win32Exception) { }
                    catch (InvalidOperationException) { }
                    catch (NotSupportedException) { }
                }
            }
        }

        internal static bool Inside(string file, string folder)
        {
            if (string.IsNullOrEmpty(file) || string.IsNullOrEmpty(folder)) return false;
            return Path.GetFullPath(file).StartsWith(Path.GetFullPath(folder).TrimEnd('\\') + "\\", StringComparison.OrdinalIgnoreCase);
        }

        // A file can stay locked for a moment after the program holding it closes.
        private static void Patiently(Action remove)
        {
            for (int tries = 1; ; tries++)
            {
                try { remove(); return; }
                catch (IOException) { if (tries == 10) throw; }
                catch (UnauthorizedAccessException) { if (tries == 10) throw; }
                System.Threading.Thread.Sleep(300);
            }
        }

        private static void DeleteShortcut(string path, string destination)
        {
            if (!File.Exists(path)) return;
            Type type = Type.GetTypeFromProgID("WScript.Shell");
            dynamic shell = Activator.CreateInstance(type);
            try
            {
                dynamic shortcut = shell.CreateShortcut(path);
                try { if (string.Equals((string)shortcut.TargetPath, Path.Combine(destination, "Capri.exe"), StringComparison.OrdinalIgnoreCase)) File.Delete(path); }
                finally { Marshal.FinalReleaseComObject(shortcut); }
            }
            finally { Marshal.FinalReleaseComObject(shell); }
        }
    }

    internal sealed class SetupWindow : Form
    {
        private readonly bool removing;
        private readonly PictureBox chip;
        private readonly Button primary;
        private readonly CheckBox desktop;
        private readonly CheckBox launch;
        private readonly Label status;
        private readonly Label stage;
        private readonly Label note;
        private readonly Button cancel;
        private long got, total;
        private bool fetching;
        private volatile bool stopping;

        internal SetupWindow(bool uninstall)
        {
            removing = uninstall;
            Text = removing ? "Remove Capri" : "Install Capri";
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            MinimizeBox = true;
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(680, 470);
            AutoScaleMode = AutoScaleMode.Dpi;
            BackColor = Color.FromArgb(19, 20, 24);
            ForeColor = Color.FromArgb(240, 241, 243);
            Font = new Font("Segoe UI", 10);
            using (Stream source = Assembly.GetExecutingAssembly().GetManifestResourceStream("Capri.Chip.ico")) if (source != null) Icon = new Icon(source);
            Label eyebrow = LabelAt("CAPRI  /  DESKTOP COMPANION", 34, 29, 590, 22, 9, Color.FromArgb(172, 248, 98));
            Label heading = LabelAt(removing ? "See you around." : "A little company.\nZero noise.", 31, 69, 408, 116, 29, ForeColor);
            heading.Font = new Font("Segoe UI Semibold", 29, FontStyle.Bold);
            chip = new PictureBox();
            chip.Bounds = new Rectangle(462, 80, 180, 112);
            chip.SizeMode = PictureBoxSizeMode.Zoom;
            using (Stream image = Assembly.GetExecutingAssembly().GetManifestResourceStream("Capri.Chip.png")) if (image != null) using (Bitmap loaded = new Bitmap(image)) chip.Image = new Bitmap(loaded);
            Controls.Add(chip);
            LabelAt(removing ? "Remove the app and its shortcuts for this Windows user.\nQuit Capri from the tray before continuing." : "Right-click for her menu.", 34, 208, 610, 53, 11, Color.FromArgb(175, 179, 188));
            desktop = new CheckBox { Text = "Add a desktop shortcut", Checked = false, AutoSize = true, Location = new Point(34, 280), Visible = !removing, ForeColor = ForeColor, BackColor = BackColor };
            launch = new CheckBox { Text = "Open Capri when finished", Checked = true, AutoSize = true, Location = new Point(34, 310), Visible = !removing, ForeColor = ForeColor, BackColor = BackColor };
            Controls.Add(desktop);
            Controls.Add(launch);
            status = LabelAt(removing ? "Your saved position and size will be kept." : "", 34, 350, 604, 22, 9, Color.FromArgb(146, 151, 161));
            stage = LabelAt("", 34, 278, 604, 28, 12.5f, ForeColor);
            stage.Visible = false;
            note = LabelAt("", 34, 310, 604, 22, 9.5f, Color.FromArgb(175, 179, 188));
            note.Visible = false;
            primary = new Button { Text = removing ? "Remove Capri" : "Install Capri", Bounds = new Rectangle(450, 399, 194, 43), FlatStyle = FlatStyle.Flat, BackColor = Color.FromArgb(239, 58, 78), ForeColor = Color.White, Cursor = Cursors.Hand };
            primary.FlatAppearance.BorderSize = 0;
            primary.Click += Perform;
            Controls.Add(primary);
            cancel = new Button { Text = "Cancel", Bounds = new Rectangle(318, 399, 116, 43), FlatStyle = FlatStyle.Flat, ForeColor = Color.FromArgb(196, 199, 205), BackColor = BackColor };
            cancel.FlatAppearance.BorderColor = Color.FromArgb(62, 65, 74);
            cancel.Click += delegate
            {
                if (!fetching) { Close(); return; }
                // Skipping the speech model still leaves Capri installed; she can fetch it later from her Mainframe.
                stopping = true;
                cancel.Enabled = false;
                status.Text = "Skipping it…";
            };
            Controls.Add(cancel);
            AcceptButton = primary;
            CancelButton = cancel;
        }

        private Label LabelAt(string text, int x, int y, int width, int height, float size, Color color)
        {
            Label label = new Label { Text = text, Bounds = new Rectangle(x, y, width, height), Font = new Font("Segoe UI", size), ForeColor = color, BackColor = Color.Transparent };
            Controls.Add(label);
            return label;
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            using (Pen line = new Pen(Color.FromArgb(56, 58, 66))) e.Graphics.DrawLine(line, 34, 382, Width - 52, 382);
            using (Pen line = new Pen(Color.FromArgb(239, 58, 78), 3)) e.Graphics.DrawLine(line, 0, 0, ClientSize.Width, 0);
            if (!fetching || total <= 0) return;
            int tall = Math.Max(4, status.Height / 5);
            Rectangle bar = new Rectangle(status.Left, status.Top - tall - 6, status.Width, tall);
            using (Brush track = new SolidBrush(Color.FromArgb(42, 44, 52))) e.Graphics.FillRectangle(track, bar);
            using (Brush fill = new SolidBrush(Color.FromArgb(239, 58, 78))) e.Graphics.FillRectangle(fill, new Rectangle(bar.X, bar.Y, (int)(bar.Width * Math.Min(1.0, (double)got / total)), bar.Height));
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            stopping = true;
            base.OnFormClosing(e);
        }

        private void Perform(object sender, EventArgs e)
        {
            primary.Enabled = false;
            try
            {
                if (removing) { Program.BeginUninstall(); Close(); return; }
                status.Text = "Making a little room for Capri…";
                Program.Install(desktop.Checked);
            }
            catch (Exception error) { status.Text = "Capri could not finish. Your files are still here."; primary.Enabled = true; MessageBox.Show(error.Message, "Capri", MessageBoxButtons.OK, MessageBoxIcon.Error); return; }
            if (Environment.Is64BitOperatingSystem && !VoicePack.Complete(VoicePack.Folder(Program.InstallPath))) FetchVoice();
            else Finish();
        }

        private void Finish()
        {
            if (launch.Checked) Process.Start(new ProcessStartInfo(Path.Combine(Program.InstallPath, "Capri.exe")) { UseShellExecute = true });
            Close();
        }

        // Her local speech model comes with the install, and the window says so plainly while it downloads.
        private void FetchVoice()
        {
            Speaking(0, VoicePack.Size, "Starting the download…");
            Thread worker = new Thread(delegate()
            {
                string failed = null;
                try
                {
                    Program.FetchVoice(Program.InstallPath, delegate(string what, long have, long size)
                    {
                        string detail = what == "Downloading" ? Program.Megabytes(have) + " of " + Program.Megabytes(size) : what == "Checking" ? "Checking the download…" : "Unpacking…";
                        Post(delegate { Speaking(have, size, detail); });
                    }, delegate { return stopping; });
                }
                catch (OperationCanceledException) { failed = ""; }
                catch (Exception error) { failed = error.Message; }
                Post(delegate { Fetched(failed); });
            });
            worker.IsBackground = true;
            worker.Start();
        }

        private void Post(Action action)
        {
            try { if (!IsDisposed) BeginInvoke(action); } catch (InvalidOperationException) { }
        }

        internal void Speaking(long have, long size, string detail)
        {
            fetching = true;
            desktop.Visible = false;
            launch.Visible = false;
            stage.Text = "Installing the local speech model";
            note.Text = "This may take some time. For a better sounding, faster voice, get an API key.";
            stage.Visible = true;
            note.Visible = true;
            if (!stopping) cancel.Text = "Skip";
            primary.Text = "Installing…";
            primary.Enabled = false;
            got = have;
            total = size;
            if (!stopping) status.Text = detail;
            Invalidate();
        }

        internal bool Saying(string heading, string warning) { return stage.Visible && note.Visible && stage.Text == heading && note.Text.StartsWith(warning, StringComparison.Ordinal) && note.Text.IndexOf("API key", StringComparison.Ordinal) >= 0; }

        private void Fetched(string failed)
        {
            fetching = false;
            if (string.IsNullOrEmpty(failed)) { Finish(); return; }
            stage.Text = "Couldn't install the local speech model";
            note.Text = "Capri is installed. Get it later in Capri Mainframe, or add an API key.";
            status.Text = failed;
            cancel.Visible = false;
            primary.Text = "Finish";
            primary.Click -= Perform;
            primary.Click += delegate { Finish(); };
            primary.Enabled = true;
            Invalidate();
        }

        protected override void Dispose(bool disposing) { if (disposing && chip.Image != null) chip.Image.Dispose(); base.Dispose(disposing); }
    }
}
