using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Threading;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Input;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Platform;
using Avalonia.Threading;

namespace CapriDesktop
{
    [DataContract]
    internal sealed class Settings
    {
        [DataMember] internal int X;
        [DataMember] internal int Y;
        [DataMember] internal bool Placed;
        [DataMember] internal double Size = 240;
        [DataMember] internal string Model;
        [DataMember] internal string Language;
        [DataMember] internal string Nickname;
        [DataMember] internal int Replies;
        [DataMember] internal int Recall;
        [DataMember] internal int Wander;
        [DataMember] internal bool NoDance;
        [DataMember] internal int? Sounds;
        [DataMember] internal string VoiceId;
        [DataMember] internal bool Silent;
        [DataMember] internal int Typing;
    }

    internal sealed class Clip
    {
        internal readonly Bitmap Strip;
        internal readonly int Count;
        internal readonly double Fps;
        internal readonly double Height;

        internal Clip(string name, int count, double fps, double height)
        {
            Count = count;
            Fps = fps;
            Height = height;
            Stream stream = typeof(Clip).Assembly.GetManifestResourceStream("Capri.Sprite." + name + ".png");
            if (stream == null) throw new FileNotFoundException("Capri's " + name + " animation is missing.");
            using (stream) Strip = new Bitmap(stream);
        }

        internal bool Even { get { return Strip.PixelSize.Width % Count == 0; } }
        internal int Wide { get { return Strip.PixelSize.Width / Count; } }

        internal bool Solid(int cell, int x, int y, int radius)
        {
            int left = cell * Wide + Math.Max(0, x - radius);
            int right = cell * Wide + Math.Min(Wide, x + radius + 1);
            int top = Math.Max(0, y - radius);
            int bottom = Math.Min(Strip.PixelSize.Height, y + radius + 1);
            if (right <= left || bottom <= top) return false;
            int wide = right - left;
            byte[] pixels = new byte[wide * (bottom - top) * 4];
            GCHandle pin = GCHandle.Alloc(pixels, GCHandleType.Pinned);
            try { Strip.CopyPixels(new PixelRect(left, top, wide, bottom - top), pin.AddrOfPinnedObject(), pixels.Length, wide * 4); }
            finally { pin.Free(); }
            for (int i = 3; i < pixels.Length; i += 4) if (pixels[i] > 24) return true;
            return false;
        }
    }

    internal sealed class Body : Control
    {
        internal Clip Shown;
        internal int Cell;
        internal Clip Mouth;
        internal int Talking = -1;
        internal double Size = 240;
        internal double Breathe;
        internal double Tilt;
        internal Point Centre;

        internal Rect Place(Clip shown)
        {
            double tall = Size * shown.Height;
            double wide = tall * shown.Wide / shown.Strip.PixelSize.Height;
            return new Rect(Centre.X - wide / 2, Centre.Y - tall / 2, wide, tall);
        }

        public override void Render(DrawingContext context)
        {
            Clip shown = Shown;
            if (shown == null) return;
            using (context.PushTransform(Matrix.CreateTranslation(-Centre.X, -Centre.Y) * Matrix.CreateRotation(Tilt * Math.PI / 180) * Matrix.CreateTranslation(Centre.X, Centre.Y + Breathe)))
            {
                Rect place = Place(shown);
                context.DrawImage(shown.Strip, new Rect(Cell * shown.Wide, 0, shown.Wide, shown.Strip.PixelSize.Height), place);
                if (Talking >= 0 && Mouth != null) context.DrawImage(Mouth.Strip, new Rect(Talking * Mouth.Wide, 0, Mouth.Wide, Mouth.Strip.PixelSize.Height), place);
            }
        }
    }

    internal sealed class Pet : Window
    {
        internal const double SitX = 0.30;
        internal const double SitY = 0.79;
        internal const double PerchLeft = 0.21;
        internal const double PerchTop = 0.17;
        internal const double PerchRight = 0.69;
        private const int BlinkFrom = 9;
        private const double StandX = 0.35;
        private const double StandY = 0.99;
        private static readonly double[] WanderPaces = { 3, 1, 0.45, 0.18 };
        internal static readonly string[] WanderNames = { "Off", "Rarely", "Sometimes", "Often", "Restless" };
        private static readonly string[] Lengths = { "Short", "Medium", "Long" };
        private static readonly int[] Recalls = { 8, 16, 32 };
        private static readonly string[] SoundNames = { "Off", "Quiet", "Normal", "Loud" };
        private static readonly int[] SoundLevels = { 0, 30, 60, 100 };
        internal const string Offline = "My brain's offline. Install Ollama from ollama.com, then run: ollama pull gemma4:e4b";
        internal const string DiscordPage = "https://discord.gg/F4fDHfGtFk";
        internal static readonly double[] Sizes = { 140, 170, 240, 320, 440, 560 };
        private static readonly string[] SizeNames = { "Tiny", "Small", "Medium", "Large", "Huge", "Giant" };
        internal readonly Brain Brain = new Brain();
        internal readonly Talk Talk;
        internal Desk Desk = Desk.Make();
        internal Func<string, string> Think;
        internal Settings Saved;
        private readonly IClassicDesktopStyleApplicationLifetime desktop;
        private readonly Clip idle = new Clip("idle", 16, 8, 1.11);
        private readonly Clip blink = new Clip("blink", 4, 8, 1.11);
        private readonly Clip blinkGlitch = new Clip("blink_glitch", 4, 8, 1.11);
        private readonly Clip glitch = new Clip("glitch", 10, 15, 1.11);
        private readonly Clip dangle = new Clip("dangle", 24, 10, 1.11);
        private readonly Clip perch = new Clip("perch", 16, 8, 1);
        private readonly Clip perchBlink = new Clip("perch_blink", 4, 8, 1);
        private readonly Clip perchBlinkGlitch = new Clip("perch_blink_glitch", 4, 8, 1);
        private readonly Clip perchGlitch = new Clip("perch_glitch", 10, 15, 1);
        private readonly Clip mouth = new Clip("mouth", 5, 12, 1.11);
        private readonly Clip walk = new Clip("walk", 12, 12, 1.11);
        private readonly Clip dance = new Clip("dance", 16, 16, 1.11);
        private readonly Clip perchDance = new Clip("perch_dance", 16, 16, 1);
        private readonly Clip arrive = new Clip("arrive", 14, 15, 1.11);
        private readonly Clip leave = new Clip("leave", 12, 15, 1.11);
        private readonly Clip perchArrive = new Clip("perch_arrive", 14, 15, 1);
        private readonly Clip perchLeave = new Clip("perch_leave", 12, 15, 1);
        private readonly Clip grin = new Clip("grin", 16, 8, 1.11);
        internal static readonly string[] PetLines = { "Heh. Okay, I'll allow it.", "Pets logged. Morale up twelve percent.", "Careful, choom. I bite viruses." };
        private readonly List<double> petTurns = new List<double>();
        private int petX;
        private int petWay;
        private double petAt = -60;
        private int petLine;
        private int petTicket;
        private readonly Body body = new Body();
        private readonly List<MenuItem> wanderPicks = new List<MenuItem>();
        private readonly List<MenuItem> languagePicks = new List<MenuItem>();
        private readonly List<MenuItem> replyPicks = new List<MenuItem>();
        private readonly List<MenuItem> recallPicks = new List<MenuItem>();
        private readonly List<MenuItem> soundPicks = new List<MenuItem>();
        private MenuItem danceItem;
        private MenuItem musicItem;
        private MenuItem brainMenu;
        internal Func<bool> MusicPlaying = Music.Playing;
        private bool musicOn;
        private bool musicBusy;
        private double musicAt;
        private bool walking;
        private int walkTo;
        private double walkStep;
        private double walkCarry;
        private bool dropAfter;
        private bool quitting;
        private bool away;
        private bool opened;
        private readonly Voice voice = new Voice();
        private VoiceListing voices;
        private string voiceProblem;
        private bool listing;
        private MenuItem voiceMenu;
        private MenuItem typingItem;
        private const int VoiceWait = 8000;
        private const string KeyHeard = "(voice key)";
        private const string NameHeard = "(nickname)";
        private const string PersonalityHeard = "(personality)";
        private bool dancing;
        private Action outro;
        private double outroAt;
        private double nextWander = -1;
        private readonly DispatcherTimer frames = new DispatcherTimer();
        private readonly Stopwatch clock = Stopwatch.StartNew();
        private readonly Random random = new Random();
        private readonly string folder;
        private Clip clip;
        private double clipStart;
        private int idleCycle;
        private Clip blinkWith;
        private double nextGlitch;
        private bool pressed;
        private bool dragging;
        private bool falling;
        private PixelPoint anchor;
        private PixelPoint grab;
        private PixelPoint downAt;
        private Point pointer = new Point(-1, -1);
        private double fallSpeed;
        private double fallStep;
        private Ledge seat;
        private Ledge fallTo;
        private double coveredSince = -1;
        private double seatLost = -1;
        private double nextCheck;
        private int asking;
        private Smoke smoke;

        internal Pet(IClassicDesktopStyleApplicationLifetime desktop)
        {
            this.desktop = desktop;
            folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Capri");
            Saved = Program.Smoke != null ? new Settings() : Load();
            Brain.LogPath = Path.Combine(Program.Smoke ?? folder, "errors.txt");
            Brain.BrainMenu = "in my right-click menu under Brain";
            Brain.Bin = OperatingSystem.IsMacOS() ? "Trash" : "trash";
            Brain.MemoryPath = Path.Combine(Program.Smoke ?? folder, "me.md");
            Brain.PersonalityPath = Path.Combine(Program.Smoke ?? folder, Brain.PersonalityName);
            Brain.MakePersonality();
            Brain.Language = Brain.LanguageCode(Saved.Language) == "en" ? null : Saved.Language;
            Brain.Nickname = Brain.Nick(Saved.Nickname).Length == 0 ? null : Brain.Nick(Saved.Nickname);
            Brain.Wordiness = Math.Max(0, Math.Min(2, Saved.Replies));
            Brain.Recall = Saved.Recall == 16 || Saved.Recall == 32 ? Saved.Recall : 8;
            Saved.Wander = Math.Max(0, Math.Min(4, Saved.Wander));
            Sfx.Level = Math.Max(0, Math.Min(100, Saved.Sounds ?? 60));
            if (Program.Smoke == null) Sfx.Out = Limbs.Sound;
            if (Program.Smoke == null) Brain.Load(Path.Combine(folder, "chat.json"));
            Limbs.Attach(Brain);
            voice.Key = ReadVoiceKey();
            voice.VoiceId = string.IsNullOrEmpty(Saved.VoiceId) ? null : Saved.VoiceId;
            if (Program.Smoke == null) { Voice.Playback = Limbs.Speak; Voice.Silence = Limbs.Hush; }
            if (Program.Smoke == null)
            {
                Sfx.TypeOut = delegate(short[] samples) { Limbs.Speak(Voice.Wave(samples, Sfx.Rate)); };
                Sfx.Untyped = Limbs.Hush;
            }
            Title = "Capri";
            SystemDecorations = SystemDecorations.None;
            Background = Brushes.Transparent;
            TransparencyLevelHint = new[] { WindowTransparencyLevel.Transparent };
            Topmost = true;
            ShowInTaskbar = false;
            CanResize = false;
            WindowStartupLocation = WindowStartupLocation.Manual;
            body.Size = Array.IndexOf(Sizes, Saved.Size) >= 0 ? Saved.Size : 240;
            body.Mouth = mouth;
            RenderOptions.SetBitmapInterpolationMode(body, BitmapInterpolationMode.HighQuality);
            Content = body;
            Fit();
            Play(idle);
            nextGlitch = 30 + random.NextDouble() * 30;
            ContextMenu = Menu();
            Talk = new Talk(this);
            frames.Interval = TimeSpan.FromMilliseconds(33);
            frames.Tick += delegate { Tick(); };
            Opened += delegate
            {
                if (opened) return;
                opened = true;
                try
                {
                    double points = Desk.Points;
                    if (points > 0 && Screens.Primary != null) Desk.Scale = Screens.Primary.Bounds.Width / points;
                }
                catch (Exception error) { Lose(error); }
                Place();
                frames.Start();
                FindBrain();
                if (Program.Smoke != null) smoke = new Smoke(this, Program.Smoke);
                else
                {
                    int ticket = asking;
                    DispatcherTimer.RunOnce(() => Greet(ticket, DateTime.Now.Hour), TimeSpan.FromMilliseconds(1800));
                }
            };
        }

        internal double Now { get { return clock.Elapsed.TotalSeconds; } }
        internal bool Falling { get { return falling; } }
        internal bool Dragging { get { return dragging; } }
        internal Ledge Seat { get { return seat; } }
        internal double BodySize { get { return body.Size; } }
        internal Clip Showing { get { return clip; } }
        internal Clip PerchGlitch { get { return perchGlitch; } }
        internal IList<Clip> Clips { get { return new[] { idle, blink, blinkGlitch, glitch, dangle, perch, perchBlink, perchBlinkGlitch, perchGlitch, mouth, walk, dance, perchDance, arrive, leave, perchArrive, perchLeave, grin }; } }
        internal Clip Arrival { get { return arrive; } }
        internal Clip PerchArrival { get { return perchArrive; } }
        internal Clip SeatDance { get { return perchDance; } }
        internal bool Arriving { get { return clip == arrive || clip == perchArrive; } }
        internal bool Walking { get { return walking; } }
        internal bool Leaving { get { return outro != null; } }
        internal bool Dancing { get { return dancing; } set { dancing = value; Sync(); } }
        internal PixelPoint Anchor { get { return anchor; } }
        internal PixelPoint Hip { get { return walking && seat != null ? new PixelPoint(anchor.X + Px(body.Size * StandX), anchor.Y + Px(body.Size * StandY)) : new PixelPoint(anchor.X + Px(body.Size * SitX), anchor.Y + Px(body.Size * SitY)); } }
        internal int Headroom { get { return (int)Math.Ceiling(body.Size * (SitY - PerchTop) * DesktopScaling); } }
        internal int Feet { get { return anchor.Y + Px(body.Size * 0.99); } }
        internal PixelPoint FeetPoint { get { return new PixelPoint(anchor.X + Px(body.Size * 0.58), Feet); } }
        internal PixelPoint Head { get { return new PixelPoint(anchor.X + Px(body.Size * 0.52), anchor.Y - Px(4)); } }
        internal PixelPoint Middle { get { return new PixelPoint(anchor.X + Px(body.Size / 2), anchor.Y + Px(body.Size / 2)); } }
        internal PixelRect Area { get { Screen screen = ScreenHere(); return screen == null ? new PixelRect(0, 0, 1920, 1080) : screen.WorkingArea; } }
        internal bool Busy { get { return dragging || pressed || falling; } }
        internal bool Hovered { get { return body.IsPointerOver && Opaque(pointer); } }
        private double FrameX { get { return body.Size * 0.08 - 8; } }
        private double FrameY { get { return -body.Size * 0.06 - 8; } }

        private int Px(double dip) { return (int)Math.Round(dip * DesktopScaling); }

        internal PixelPoint Seated(int x, int y) { return new PixelPoint(x - Px(body.Size * SitX), y - Px(body.Size * SitY)); }

        private PixelPoint Standing(int x, int y) { return new PixelPoint(x - Px(body.Size * StandX), y - Px(body.Size * StandY)); }

        internal void Move(PixelPoint to)
        {
            anchor = to;
            Position = new PixelPoint(to.X + Px(FrameX), to.Y + Px(FrameY));
        }

        private void Fit()
        {
            Width = Math.Ceiling(body.Size * 0.77 + 16);
            Height = Math.Ceiling(body.Size * 1.12 + 16);
            body.Centre = new Point(body.Size / 2 - FrameX, body.Size / 2 - FrameY);
        }

        internal Screen ScreenHere()
        {
            PixelPoint hip = Hip;
            Screen screen = Screens.ScreenFromPoint(new PixelPoint(hip.X, hip.Y - 1));
            if (screen == null) screen = Screens.ScreenFromWindow(this);
            return screen ?? Screens.Primary;
        }

        private PixelRect ScreenAt(PixelPoint point)
        {
            Screen screen = Screens.ScreenFromPoint(point) ?? Screens.Primary;
            return screen == null ? new PixelRect(0, 0, 1920, 1080) : screen.Bounds;
        }

        private PixelRect WorkAt(PixelPoint point)
        {
            Screen screen = Screens.ScreenFromPoint(point) ?? ScreenHere();
            return screen == null ? new PixelRect(0, 0, 1920, 1080) : screen.WorkingArea;
        }

        private void Place()
        {
            Screen saved = Saved.Placed ? Screens.ScreenFromPoint(new PixelPoint(Saved.X + Px(body.Size / 2), Saved.Y + Px(body.Size / 2))) : null;
            Screen screen = saved ?? Screens.Primary ?? (Screens.All.Count > 0 ? Screens.All[0] : null);
            if (screen == null) return;
            PixelRect area = screen.WorkingArea;
            Move(new PixelPoint(area.X + (area.Width - Px(body.Size)) / 2, area.Y + (area.Height - Px(body.Size)) / 2));
            Arrive();
        }

        internal void Bring()
        {
            Screen screen = Screens.Primary ?? ScreenHere();
            if (screen == null || quitting) return;
            Halt();
            away = false;
            if (!IsVisible) Show();
            walking = false;
            seat = null;
            falling = false;
            PixelRect area = screen.WorkingArea;
            Move(new PixelPoint(area.X + area.Width / 2 - Px(body.Size / 2), area.Y));
            Arrive();
        }

        internal bool Away { get { return away; } }

        internal void HideAway()
        {
            if (away || quitting) return;
            away = true;
            Talk.Dismiss();
            GlitchOut(delegate { seat = null; walking = false; Hide(); });
        }

        internal void ComeBack()
        {
            if (!away || quitting) return;
            Halt();
            away = false;
            if (!IsVisible) Show();
            walking = false;
            seat = null;
            falling = false;
            Arrive();
        }

        internal int Arrivals;

        private void Arrive(bool quiet = false)
        {
            Arrivals++;
            Play(arrive);
            dropAfter = true;
            if (!quiet) Sfx.Play(Cue.Arrive);
        }

        private void Tick()
        {
            double now = Now;
            if (outro != null && now >= outroAt) { Action next = outro; outro = null; next(); }
            if (away && !IsVisible) { if (smoke != null) smoke.Tick(now); return; }
            if (!dragging)
            {
                if (falling) Tumble(now);
                else if (seat != null) HoldSeat(now);
            }
            if (walking) Walk(now);
            if (now >= nextCheck && !away)
            {
                nextCheck = now + 0.25;
                CheckSeat(now);
                CheckWander(now);
                CheckMusic(now);
            }
            Advance(now);
            Talk.Update(now);
            if (smoke != null) smoke.Tick(now);
        }

        private void Play(Clip next)
        {
            clip = next;
            clipStart = Now;
            idleCycle = 0;
            blinkWith = null;
        }

        private void Advance(double now)
        {
            bool seated = clip == perch || clip == perchGlitch || clip == perchDance || clip == perchArrive || clip == perchLeave;
            bool perched = seat != null;
            if (perched && !seated && !walking) Play(perch);
            if ((!perched && seated) || (clip == dangle && !dragging && !falling)) Play(idle);
            int index = (int)((now - clipStart) * clip.Fps);
            if (clip == leave || clip == perchLeave) index = Math.Min(index, clip.Count - 1);
            if ((clip == glitch && index >= glitch.Count) || (clip == grin && index >= grin.Count * 2)) { Play(idle); index = 0; }
            if (clip == arrive && index >= arrive.Count)
            {
                Play(idle);
                index = 0;
                if (dropAfter) { dropAfter = false; Fall(); }
            }
            if ((clip == perchGlitch && index >= perchGlitch.Count) || (clip == perchArrive && index >= perchArrive.Count)) { Play(perch); index = 0; }
            if (dropAfter && clip != arrive && clip != glitch)
            {
                dropAfter = false;
                if (seat == null && !dragging && !falling) Fall();
            }
            bool still = !walking && !dragging && !falling && outro == null && Talk.Quiet;
            bool grooving = dancing || (musicOn && !Saved.NoDance);
            if (grooving && still && (clip == idle || clip == perch)) { Play(clip == perch ? perchDance : dance); index = 0; }
            if ((!grooving || !still) && (clip == dance || clip == perchDance)) { Play(clip == perchDance ? perch : idle); index = 0; }
            if (clip == idle || clip == perch)
            {
                int cycle = index / clip.Count;
                if (cycle != idleCycle)
                {
                    idleCycle = cycle;
                    double roll = random.NextDouble();
                    blinkWith = roll < 0.1 ? (clip == perch ? perchBlinkGlitch : blinkGlitch) : roll < 0.55 ? (clip == perch ? perchBlink : blink) : null;
                    if (now >= nextGlitch && Talk.Quiet)
                    {
                        nextGlitch = now + 30 + random.NextDouble() * 30;
                        Play(clip == perch ? perchGlitch : glitch);
                        index = 0;
                    }
                }
            }
            int cell = index % clip.Count;
            Clip shown = clip;
            if ((clip == idle || clip == perch) && blinkWith != null && cell >= BlinkFrom && cell < BlinkFrom + blinkWith.Count) { shown = blinkWith; cell -= BlinkFrom; }
            bool settled = !dragging && clip != dangle;
            body.Breathe = settled ? Math.Sin(now * 1.75) * 2.2 : 0;
            body.Tilt = settled ? Math.Sin(now * 0.95) * 0.65 : 0;
            body.Shown = shown;
            body.Cell = cell;
            body.Talking = clip == idle && shown != blinkGlitch ? Talk.MouthCell(now) : -1;
            body.InvalidateVisual();
        }

        private List<long> Ours()
        {
            List<long> skip = new List<long>();
            foreach (TopLevel window in new TopLevel[] { this, Talk.Bubble, Talk.Typer, Talk.Pill })
            {
                IPlatformHandle handle = window.TryGetPlatformHandle();
                if (handle != null) skip.Add(handle.Handle.ToInt64());
            }
            return skip;
        }

        internal string DeskTrouble;

        private void Lose(Exception error)
        {
            DeskTrouble = error.GetType().Name + ": " + error.Message;
            Desk = new BareDesk();
        }

        internal List<Spot> Windows()
        {
            try { return Desk.Windows(Ours()); }
            catch (Exception error) { Lose(error); return new List<Spot>(); }
        }

        private Spot Find(long window)
        {
            try { return Desk.Find(window); }
            catch (Exception error) { Lose(error); return null; }
        }

        private bool Alive(long window)
        {
            try { return Desk.Alive(window); }
            catch (Exception error) { Lose(error); return false; }
        }

        internal List<Ledge> Ledges()
        {
            List<PixelRect> floors = new List<PixelRect>();
            Desk.Screens.Clear();
            foreach (Screen screen in Screens.All)
            {
                floors.Add(screen.WorkingArea);
                Desk.Screens.Add(screen.Bounds);
            }
            return Perches.Find(Windows(), floors);
        }

        internal Ledge Perch(IEnumerable<Ledge> ledges, PixelPoint hip)
        {
            return Perches.Below(ledges, hip, Px(Math.Max(32, body.Size * 0.45)), Px(Math.Max(160, body.Size)), Px(24), Headroom, ScreenAt);
        }

        internal void Fall()
        {
            walking = false;
            seat = null;
            fallTo = null;
            falling = true;
            fallSpeed = 0;
            fallStep = Now;
            Play(dangle);
        }

        private void Tumble(double now)
        {
            double step = Math.Max(0, Math.Min(0.05, now - fallStep));
            fallStep = now;
            PixelPoint hip = Hip;
            if (fallTo == null) fallTo = Perches.Below(Ledges(), hip, 0, int.MaxValue, 0, Headroom, ScreenAt);
            if (fallTo == null) { falling = false; Save(); return; }
            fallSpeed += body.Size * 11 * step;
            int drop = (int)Math.Ceiling(fallSpeed * step * DesktopScaling);
            if (hip.Y + drop >= fallTo.Y) Land(fallTo);
            else Move(new PixelPoint(anchor.X, anchor.Y + drop));
        }

        private void Land(Ledge ledge)
        {
            fallTo = null;
            walking = false;
            if (ledge.Window != 0)
            {
                Spot spot = Find(ledge.Window);
                if (spot == null || !spot.Seat) return;
                if (spot.Bounds != ledge.Bounds) { ledge.Left = spot.Bounds.X; ledge.Right = spot.Bounds.Right; ledge.Y = spot.Bounds.Y; ledge.Bounds = spot.Bounds; }
            }
            falling = false;
            coveredSince = -1;
            seatLost = -1;
            int x = Math.Max(ledge.Left + Px(8), Math.Min(Hip.X, ledge.Right - Px(8)));
            PixelRect area = WorkAt(new PixelPoint(x, ledge.Y - 1));
            int rightmost = area.Right - (int)Math.Ceiling(body.Size * (PerchRight - SitX) * DesktopScaling);
            x = Math.Max(area.X + (int)Math.Ceiling(body.Size * (SitX - PerchLeft) * DesktopScaling), Math.Min(x, rightmost));
            seat = ledge;
            Move(Seated(x, ledge.Y));
            Play(perchGlitch);
            Save();
        }

        internal void HoldSeat(double now)
        {
            if (seat == null || seat.Window == 0) return;
            Spot spot = Find(seat.Window);
            if (spot == null || !spot.Seat)
            {
                if (seatLost < 0) seatLost = now;
                if (!Alive(seat.Window) || now - seatLost >= 0.3) StandUp();
                return;
            }
            seatLost = -1;
            PixelRect bounds = spot.Bounds;
            if (bounds == seat.Bounds) return;
            EndWalk();
            int dx =bounds.Width == seat.Bounds.Width ? bounds.X - seat.Bounds.X : 0;
            int x = Math.Max(bounds.X + Px(8), Math.Min(Hip.X + dx, bounds.Right - Px(8)));
            seat.Bounds = bounds;
            seat.Left = bounds.X;
            seat.Right = bounds.Right;
            seat.Y = bounds.Y;
            Move(Seated(x, bounds.Y));
        }

        internal void CheckSeat(double now)
        {
            if (seat == null || falling || dragging || seat.Floor) return;
            if (!Perches.Covered(Windows(), seat, Hip.X)) { coveredSince = -1; return; }
            if (coveredSince < 0) coveredSince = now;
            if (now - coveredSince >= 0.7) StandUp();
        }

        internal void StandUp()
        {
            Halt();
            walking = false;
            seat = null;
            coveredSince = -1;
            seatLost = -1;
            falling = false;
            fallTo = null;
            Play(glitch);
        }

        private void SitHere()
        {
            Halt();
            EndWalk();
            PixelPoint hip = Hip;
            Land(new Ledge { Left = hip.X - Px(8), Right = hip.X + Px(8), Y = hip.Y, Floor = true, Free = true });
        }

        private bool Opaque(Point at)
        {
            Clip shown = body.Shown;
            if (shown == null || at.X < 0) return false;
            Rect place = body.Place(shown);
            double x = at.X - place.X;
            double y = at.Y - body.Breathe - place.Y;
            if (x < 0 || y < 0 || x >= place.Width || y >= place.Height) return false;
            return shown.Solid(body.Cell, (int)(x / place.Width * shown.Wide), (int)(y / place.Height * shown.Strip.PixelSize.Height), 6);
        }

        protected override void OnPointerPressed(PointerPressedEventArgs e)
        {
            base.OnPointerPressed(e);
            if (!e.GetCurrentPoint(this).Properties.IsLeftButtonPressed || outro != null) return;
            Point at = e.GetPosition(this);
            if (!Opaque(at)) return;
            pressed = true;
            dragging = false;
            downAt = this.PointToScreen(at);
            grab = new PixelPoint(downAt.X - anchor.X, downAt.Y - anchor.Y);
            e.Pointer.Capture(this);
        }

        protected override void OnPointerMoved(PointerEventArgs e)
        {
            base.OnPointerMoved(e);
            pointer = e.GetPosition(this);
            if (!pressed) { Stroke(this.PointToScreen(pointer)); return; }
            PixelPoint at = this.PointToScreen(pointer);
            int dx = at.X - downAt.X;
            int dy = at.Y - downAt.Y;
            if (!dragging && dx * dx + dy * dy < 25) return;
            if (!dragging) Grab();
            Move(new PixelPoint(at.X - grab.X, at.Y - grab.Y));
        }

        protected override void OnPointerExited(PointerEventArgs e)
        {
            base.OnPointerExited(e);
            pointer = new Point(-1, -1);
        }

        protected override void OnPointerReleased(PointerReleasedEventArgs e)
        {
            base.OnPointerReleased(e);
            if (!pressed || e.InitialPressMouseButton != MouseButton.Left) return;
            pressed = false;
            e.Pointer.Capture(null);
            if (dragging) Drop();
            else React();
        }

        internal int Pets { get { return petTicket; } }

        internal bool Smiling { get { return seat != null ? clip == perch && blinkWith == perchBlink : clip == grin; } }

        internal Point? BodySpot()
        {
            for (double y = Bounds.Height / 4; y < Bounds.Height * 3 / 4; y += 3)
                for (double x = 20; x < Bounds.Width - 20; x += 3)
                    if (Opaque(new Point(x - 12, y)) && Opaque(new Point(x + 12, y))) return new Point(x, y);
            return null;
        }

        internal void Stroke(PixelPoint cursor)
        {
            if (pressed || dragging || falling || outro != null || Talk.Typing || !Opaque(this.PointToClient(cursor))) { petTurns.Clear(); petWay = 0; return; }
            int dx = cursor.X - petX;
            if (Math.Abs(dx) < 6) return;
            double now = Now;
            int way = Math.Sign(dx);
            petX = cursor.X;
            if (petWay != 0 && way != petWay) petTurns.Add(now);
            petWay = way;
            petTurns.RemoveAll(at => now - at > 1.5);
            if (petTurns.Count < 4 || now - petAt < 20) return;
            petTurns.Clear();
            petAt = now;
            Petted();
        }

        private void Petted()
        {
            EndWalk();
            if (seat != null) { Play(perch); blinkWith = perchBlink; }
            else Play(grin);
            int ticket = ++petTicket;
            DispatcherTimer.RunOnce(() => PetLine(ticket), TimeSpan.FromMilliseconds(1300));
        }

        internal void Greet(int ticket, int hour)
        {
            string line = Brain.Greeting(hour);
            if (line == null || ticket != asking || outro != null || !Talk.Quiet) return;
            byte[] hello = Hello(Brain.GreetingClip(hour));
            if (hello == null) { Reply(++asking, "startup", line); return; }
            ++asking;
            Talk.Answer("startup", line, Voice.Seconds(hello));
            voice.Play(hello);
        }

        internal static byte[] Hello(string name)
        {
            using (Stream clip = typeof(Pet).Assembly.GetManifestResourceStream("Capri.Voice.hello-" + name + ".wav"))
            {
                if (clip == null) return null;
                using (MemoryStream copy = new MemoryStream()) { clip.CopyTo(copy); return copy.ToArray(); }
            }
        }

        internal void PetLine(int ticket)
        {
            if (ticket != petTicket || outro != null || !Talk.Quiet) return;
            if (clip == grin) Play(idle);
            string line = PetLines[petLine++ % PetLines.Length];
            Talk.Answer("pets you", line);
            TypeAlong(line, ticket);
        }

        internal void ForgetPets()
        {
            petTicket++;
            petAt = -60;
            petTurns.Clear();
            Play(seat != null ? perch : idle);
        }

        internal void React()
        {
            Play(seat != null ? perchGlitch : glitch);
        }

        internal void NoVoice()
        {
            Talk.Answer("(voice)", "Voice chat isn't on Mac and Linux yet. Type to me for now, choom.");
        }

        internal void Grab()
        {
            Halt();
            walking = false;
            dropAfter = false;
            seat = null;
            fallTo = null;
            falling = false;
            dragging = true;
            Play(dangle);
            Talk.Dismiss();
        }

        internal bool Drop()
        {
            dragging = false;
            Ledge target = Perch(Ledges(), Hip);
            if (target == null) { Save(); return false; }
            falling = true;
            fallTo = target;
            fallSpeed = 0;
            fallStep = Now;
            return true;
        }

        internal void Resize(double size)
        {
            EndWalk();
            PixelPoint hip = Hip;
            PixelPoint middle = Middle;
            body.Size = size;
            Saved.Size = size;
            Fit();
            if (seat != null) Move(Seated(hip.X, seat.Y));
            else Move(new PixelPoint(middle.X - Px(size / 2), middle.Y - Px(size / 2)));
            body.InvalidateVisual();
            Save();
        }

        private ContextMenu Menu()
        {
            ContextMenu menu = new ContextMenu();
            MenuItem talk = new MenuItem { Header = "Talk to her" };
            talk.Click += delegate { Talk.Open(); };
            MenuItem sit = new MenuItem { Header = "Sit here" };
            sit.Click += delegate { if (seat != null && seat.Free) StandUp(); else SitHere(); };
            MenuItem move = new MenuItem { Header = "Move" };
            for (int i = 0; i < WanderNames.Length; i++)
            {
                int level = i;
                MenuItem pick = new MenuItem { Header = i == 0 ? "Stay put" : "Wander: " + WanderNames[i], ToggleType = MenuItemToggleType.Radio, GroupName = "wander" };
                pick.Click += delegate { SetWander(level); };
                wanderPicks.Add(pick);
                move.Items.Add(pick);
            }
            move.Items.Add(new Separator());
            musicItem = new MenuItem { Header = "Dance to music", ToggleType = MenuItemToggleType.CheckBox };
            musicItem.Click += delegate { SetDanceToMusic(Saved.NoDance); };
            move.Items.Add(musicItem);
            danceItem = new MenuItem { Header = "Dance", ToggleType = MenuItemToggleType.CheckBox };
            danceItem.Click += delegate { Dancing = !dancing; };
            move.Items.Add(danceItem);
            brainMenu = new MenuItem { Header = "Brain" };
            brainMenu.Items.Add(new MenuItem { Header = "Looking for models...", IsEnabled = false });
            MenuItem settings = new MenuItem { Header = "Settings" };
            MenuItem language = new MenuItem { Header = "Language" };
            for (int i = 0; i < Brain.Languages.GetLength(0); i++)
            {
                string name = Brain.Languages[i, 1];
                MenuItem pick = new MenuItem { Header = Brain.Languages[i, 0], ToggleType = MenuItemToggleType.Radio, GroupName = "language", Tag = name };
                pick.Click += delegate { SetLanguage(name); };
                languagePicks.Add(pick);
                language.Items.Add(pick);
            }
            MenuItem replies = new MenuItem { Header = "Reply length" };
            MenuItem recall = new MenuItem { Header = "Chat memory" };
            for (int i = 0; i < Lengths.Length; i++)
            {
                int level = i;
                MenuItem reply = new MenuItem { Header = Lengths[i], ToggleType = MenuItemToggleType.Radio, GroupName = "replies" };
                reply.Click += delegate { SetReplies(level); };
                replyPicks.Add(reply);
                replies.Items.Add(reply);
                MenuItem keep = new MenuItem { Header = Lengths[i], ToggleType = MenuItemToggleType.Radio, GroupName = "recall" };
                keep.Click += delegate { SetRecall(Recalls[level]); };
                recallPicks.Add(keep);
                recall.Items.Add(keep);
            }
            MenuItem sounds = new MenuItem { Header = "Sounds" };
            for (int i = 0; i < SoundNames.Length; i++)
            {
                int level = SoundLevels[i];
                MenuItem pick = new MenuItem { Header = SoundNames[i], ToggleType = MenuItemToggleType.Radio, GroupName = "sounds" };
                pick.Click += delegate { SetSounds(level); };
                soundPicks.Add(pick);
                sounds.Items.Add(pick);
            }
            sounds.Items.Add(new Separator());
            typingItem = new MenuItem { Header = "Typing sound", ToggleType = MenuItemToggleType.CheckBox };
            typingItem.Click += delegate { SetTyping(!TypingSound); };
            sounds.Items.Add(typingItem);
            MenuItem notes = new MenuItem { Header = "Open her notes (me.md)" };
            notes.Click += delegate { OpenNotes(); };
            MenuItem personality = new MenuItem { Header = "Edit her personality (personality.md)" };
            personality.Click += delegate { OpenPersonality(); };
            MenuItem resetPersonality = new MenuItem { Header = "Reset her personality to default" };
            resetPersonality.Click += delegate { ResetPersonality(); };
            MenuItem nickname = new MenuItem { Header = "What should she call you?" };
            nickname.Click += delegate { AskNickname(); };
            settings.Items.Add(nickname);
            settings.Items.Add(language);
            settings.Items.Add(replies);
            settings.Items.Add(recall);
            settings.Items.Add(sounds);
            voiceMenu = new MenuItem { Header = "Voice" };
            settings.Items.Add(voiceMenu);
            settings.Items.Add(notes);
            settings.Items.Add(personality);
            settings.Items.Add(resetPersonality);
            MenuItem sizes = new MenuItem { Header = "Size" };
            for (int i = 0; i < Sizes.Length; i++)
            {
                double size = Sizes[i];
                MenuItem pick = new MenuItem { Header = SizeNames[i] };
                pick.Click += delegate { Resize(size); };
                sizes.Items.Add(pick);
            }
            MenuItem discord = new MenuItem { Header = "Join the Discord" };
            discord.Click += delegate { Browse(DiscordPage); };
            MenuItem quit = new MenuItem { Header = "Quit Capri" };
            quit.Click += delegate { Quit(); };
            menu.Items.Add(talk);
            menu.Items.Add(sit);
            menu.Items.Add(move);
            menu.Items.Add(sizes);
            menu.Items.Add(brainMenu);
            menu.Items.Add(settings);
            menu.Items.Add(discord);
            menu.Items.Add(new Separator());
            menu.Items.Add(quit);
            menu.Opening += delegate { Sync(); RefreshBrains(); RefreshVoice(); };
            return menu;
        }

        private void Sync()
        {
            if (danceItem == null) return;
            for (int i = 0; i < wanderPicks.Count; i++) wanderPicks[i].IsChecked = i == Saved.Wander;
            danceItem.IsChecked = dancing;
            musicItem.IsChecked = !Saved.NoDance;
            string language = Brain.Language ?? "English";
            foreach (MenuItem pick in languagePicks) pick.IsChecked = (string)pick.Tag == language;
            for (int i = 0; i < replyPicks.Count; i++) replyPicks[i].IsChecked = i == Brain.Wordiness;
            for (int i = 0; i < recallPicks.Count; i++) recallPicks[i].IsChecked = Recalls[i] == Brain.Recall;
            for (int i = 0; i < soundPicks.Count; i++) soundPicks[i].IsChecked = SoundLevels[i] == Sfx.Level;
            typingItem.IsChecked = TypingSound;
        }

        internal string Picked
        {
            get
            {
                List<string> on = new List<string>();
                foreach (List<MenuItem> group in new[] { wanderPicks, languagePicks, replyPicks, recallPicks, soundPicks })
                    foreach (MenuItem pick in group) if (pick.IsChecked) on.Add(pick.Header.ToString());
                if (musicItem.IsChecked) on.Add("Dance to music");
                if (danceItem.IsChecked) on.Add("Dance");
                return string.Join(" | ", on);
            }
        }

        internal void SetDanceToMusic(bool on)
        {
            Saved.NoDance = !on;
            Save();
            Sync();
        }

        private void CheckMusic(double now)
        {
            if (Saved.NoDance || musicBusy || now < musicAt || Program.Smoke != null) return;
            musicAt = now + 2;
            musicBusy = true;
            Func<bool> playing = MusicPlaying;
            ThreadPool.QueueUserWorkItem(delegate
            {
                bool on = false;
                try { on = playing(); }
                catch (Exception) { }
                Dispatcher.UIThread.Post(delegate { musicBusy = false; HearMusic(on); });
            });
        }

        internal void HearMusic(bool on) { musicOn = on; }

        internal void SetWander(int level)
        {
            Saved.Wander = Math.Max(0, Math.Min(4, level));
            nextWander = -1;
            Save();
            Sync();
        }

        internal void AskNickname()
        {
            ComeBack();
            Talk.AskFor(NameHeard, Brain.Nickname == null ? "What should I call you? Type a name and press Enter." : "I call you " + Brain.Nickname + ". Type a new name and press Enter, or choom to go back.", SetNickname);
        }

        internal void SetNickname(string typed)
        {
            string name = Brain.Nick(typed);
            bool callable = Brain.Callable(name);
            if (callable)
            {
                Brain.Nickname = name.Length == 0 ? null : name;
                Saved.Nickname = Brain.Nickname;
                Save();
            }
            Reply(++asking, NameHeard, callable ? Brain.Renamed(name) : Brain.NotCalling);
        }

        internal void SetLanguage(string language)
        {
            Brain.Language = Brain.LanguageCode(language) == "en" ? null : language;
            Saved.Language = Brain.Language;
            Brain.Forget();
            Save();
            Sync();
        }

        internal void SetReplies(int level)
        {
            Brain.Wordiness = Math.Max(0, Math.Min(2, level));
            Saved.Replies = Brain.Wordiness;
            Save();
            Sync();
        }

        internal void SetRecall(int count)
        {
            Brain.Recall = count == 16 || count == 32 ? count : 8;
            Saved.Recall = Brain.Recall;
            Save();
            Sync();
        }

        internal void SetSounds(int level)
        {
            Sfx.Level = Math.Max(0, Math.Min(100, level));
            Saved.Sounds = Sfx.Level;
            Save();
            Sync();
            Sfx.Play(Cue.Hop);
        }

        private void RefreshBrains()
        {
            ThreadPool.QueueUserWorkItem(delegate
            {
                string[] found = null;
                try { found = Brain.InstalledModels(); }
                catch (Exception) { }
                Dispatcher.UIThread.Post(delegate { ShowBrains(found); });
            });
        }

        internal void ShowBrains(string[] found)
        {
            brainMenu.Items.Clear();
            if (found == null || found.Length == 0)
            {
                brainMenu.Items.Add(new MenuItem { Header = "No models yet. Install Ollama, then pull one.", IsEnabled = false });
                return;
            }
            foreach (string name in found)
            {
                string model = name;
                MenuItem pick = new MenuItem { Header = model, ToggleType = MenuItemToggleType.Radio, GroupName = "brain", IsChecked = model == Brain.Model };
                pick.Click += delegate { PickBrain(model); };
                brainMenu.Items.Add(pick);
            }
        }

        internal int BrainChoices { get { return brainMenu.Items.Count; } }

        internal void PickBrain(string model)
        {
            if (string.IsNullOrEmpty(model) || model == Brain.Model) return;
            Brain.Cancel();
            Brain.Model = model;
            Brain.Forget();
            Saved.Model = model;
            Save();
        }

        internal string OpenNotes()
        {
            string path = Brain.MemoryPath;
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                if (!File.Exists(path)) File.WriteAllText(path, Brain.NotesHead);
            }
            catch (Exception) { return null; }
            if (Program.Smoke == null) Browse(path);
            return path;
        }

        internal string OpenPersonality()
        {
            string path = Brain.PersonalityPath;
            if (!Brain.MakePersonality()) return null;
            if (Program.Smoke == null) Browse(path);
            return path;
        }

        internal void ResetPersonality()
        {
            ComeBack();
            string backup = Brain.ResetPersonality();
            Reply(++asking, PersonalityHeard, backup == null ? "Couldn't reset my personality. It lives in " + Brain.PersonalityPath + "." : backup.Length > 0 ? Brain.ResetKept : Brain.ResetDone);
        }

        internal static int WalkTarget(int at, int from, int to, int size, double side, double length)
        {
            int shortest = size / 2;
            int longest = size * 3;
            bool right = side < 0.5;
            if ((right ? to - at : at - from) < shortest) right = !right;
            int room = Math.Min(longest, right ? to - at : at - from);
            if (room < shortest) return at;
            int step = shortest + (int)((room - shortest) * length);
            return right ? at + step : at - step;
        }

        internal bool Stroll()
        {
            if (walking || falling || dragging || outro != null || seat == null || seat.Free) return false;
            PixelPoint hip = Hip;
            PixelRect area = WorkAt(hip);
            if (seat.Y - Px(body.Size * StandY) < ScreenAt(hip).Y) return false;
            int from = Math.Max(seat.Left, area.X) + Px(body.Size * 0.1) + Px(8);
            int to = Math.Min(seat.Right, area.Right) - Px(body.Size * 0.41) - Px(8);
            int target = WalkTarget(hip.X, from, to, Px(body.Size), random.NextDouble(), random.NextDouble());
            if (target == hip.X) return false;
            PixelPoint standing = Standing(hip.X, seat.Y);
            walkTo = standing.X + target - hip.X;
            walking = true;
            Move(standing);
            walkStep = Now;
            walkCarry = 0;
            Play(walk);
            return true;
        }

        private void Walk(double now)
        {
            double step = Math.Max(0, Math.Min(0.05, now - walkStep));
            walkStep = now;
            int gap = walkTo - anchor.X;
            walkCarry += body.Size * 0.42 * step * DesktopScaling;
            int stride = (int)walkCarry;
            walkCarry -= stride;
            if (Math.Abs(gap) <= stride) { Move(new PixelPoint(walkTo, anchor.Y)); EndWalk(); return; }
            Move(new PixelPoint(anchor.X + Math.Sign(gap) * stride, anchor.Y));
        }

        private void EndWalk()
        {
            if (!walking) return;
            PixelPoint hip = Hip;
            walking = false;
            if (seat != null) { Move(Seated(hip.X, seat.Y)); Play(perchGlitch); }
            else Play(idle);
            Save();
        }

        private void CheckWander(double now)
        {
            if (Saved.Wander <= 0) { nextWander = -1; return; }
            if (dragging || pressed || falling || walking || outro != null || quitting || seat == null || seat.Free || !Talk.Quiet) return;
            double pace = WanderPaces[Saved.Wander - 1];
            if (nextWander < 0) nextWander = now + (20 + random.NextDouble() * 40) * pace;
            if (now < nextWander) return;
            nextWander = now + (45 + random.NextDouble() * 75) * pace;
            if (random.NextDouble() < 0.5 && Blink()) return;
            Wander();
        }

        internal bool Blink()
        {
            if (walking || falling || dragging || outro != null || quitting || seat == null || seat.Free) return false;
            PixelPoint hip = Hip;
            PixelRect area = WorkAt(hip);
            int from = Math.Max(seat.Left, area.X) + Px(body.Size * 0.1) + Px(8);
            int to = Math.Min(seat.Right, area.Right) - Px(body.Size * 0.41) - Px(8);
            int target = WalkTarget(hip.X, from, to, Px(body.Size), random.NextDouble(), random.NextDouble());
            if (target == hip.X) return false;
            Ledge spot = seat;
            GlitchOut(delegate { Hop(spot, target); }, true);
            return true;
        }

        internal bool Wander()
        {
            if (outro != null || quitting) return false;
            List<Ledge> spots = new List<Ledge>();
            foreach (Ledge ledge in Ledges())
            {
                if (ledge.Right - ledge.Left < Px(body.Size / 2)) continue;
                if (seat != null && ledge.Window == seat.Window && ledge.Floor == seat.Floor && ledge.Y == seat.Y) continue;
                if (ledge.Y - Headroom < ScreenAt(new PixelPoint(ledge.Left, ledge.Y)).Y) continue;
                spots.Add(ledge);
            }
            if (spots.Count == 0) return false;
            Ledge spot = spots[random.Next(spots.Count)];
            int margin = Px(body.Size / 4);
            int x = spot.Left + margin + random.Next(Math.Max(1, spot.Right - spot.Left - 2 * margin));
            GlitchOut(delegate { Hop(spot, x); }, true);
            return true;
        }

        private void Hop(Ledge spot, int x)
        {
            walking = false;
            seat = null;
            Move(Seated(x, spot.Y));
            Land(spot);
            if (seat == spot) Play(perchArrive);
            else Arrive(true);
        }

        internal void GlitchOut(Action then, bool hop = false)
        {
            EndWalk();
            falling = false;
            fallTo = null;
            dropAfter = false;
            Play(seat != null ? perchLeave : leave);
            Sfx.Play(hop ? Cue.Hop : Cue.Leave);
            outro = then;
            outroAt = clipStart + (clip.Count + 1) / clip.Fps;
        }

        private void Halt()
        {
            if (outro == null || quitting) return;
            outro = null;
            if (clip == leave || clip == perchLeave) Play(seat != null ? perch : idle);
        }

        internal static void Browse(string url)
        {
            try
            {
                if (OperatingSystem.IsMacOS()) Process.Start("open", url);
                else if (OperatingSystem.IsLinux()) Process.Start("xdg-open", url);
                else Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
            }
            catch (Exception) { }
        }

        internal void FindBrain()
        {
            string saved = Saved.Model;
            ThreadPool.QueueUserWorkItem(delegate
            {
                string model = null;
                try { model = Brain.PickModel(Brain.InstalledModels(), saved, null); }
                catch (Exception) { }
                Dispatcher.UIThread.Post(delegate
                {
                    if (Brain.Model != null || model == null) return;
                    Brain.Model = model;
                    Saved.Model = model;
                    Save();
                });
            });
        }

        internal void Hear(string text)
        {
            text = (text ?? "").Trim();
            if (text.Length == 0) return;
            if (text.Length > 240) text = text.Substring(0, 240);
            int ticket = ++asking;
            Brain.Cancel();
            voice.Stop();
            string blocked = Safety.Screen(text);
            if (blocked != null) { Reply(ticket, text, blocked); return; }
            bool askPlease;
            string heard = text;
            text = Hands.Manners(text, out askPlease);
            if (askPlease) { Reply(ticket, heard, Hands.SayPlease); return; }
            string tool;
            ToolArguments args;
            bool noted;
            try { noted = Hands.Heard(text, out tool, out args) && tool == "remember"; }
            catch (Exception) { noted = false; tool = null; args = null; }
            if (noted)
            {
                string done = Hands.Do(tool, args);
                Brain.Remember(text, done);
                Reply(ticket, text, done);
                return;
            }
            Func<string, string> think = Think;
            if (think == null)
            {
                if (Brain.Model == null) { Reply(ticket, text, Offline); FindBrain(); return; }
                think = Brain.Ask;
            }
            Talk.Thinking(text);
            ThreadPool.QueueUserWorkItem(delegate
            {
                string said;
                try { said = Safety.Filter(think(text)); }
                catch (BrainException error) { said = error.Message == Brain.Offline ? Offline : error.Told ? error.Message : "My brain couldn't answer: " + error.Message; }
                catch (Exception error) { said = Brain.Explain(error) ?? "That reply was cancelled. Try again."; }
                if (string.IsNullOrWhiteSpace(said)) said = "My brain sent an empty reply. Try again.";
                Dispatcher.UIThread.Post(delegate { if (ticket == asking) Reply(ticket, text, said); });
            });
        }

        internal bool Speaks { get { return voice.Ready && !Saved.Silent; } }

        private void Reply(int ticket, string heard, string said)
        {
            if (!Speaks || Voice.Spoken(said).Length == 0) { Talk.Answer(heard, said); TypeAlong(said, ticket); return; }
            Talk.Thinking(heard);
            bool[] shown = { false };
            DispatcherTimer.RunOnce(delegate
            {
                if (ticket != asking || shown[0]) return;
                shown[0] = true;
                Talk.Answer(heard, said);
                TypeAlong(said, ticket);
            }, TimeSpan.FromMilliseconds(VoiceWait));
            ThreadPool.QueueUserWorkItem(delegate
            {
                byte[] wav = null;
                string problem = null;
                try { wav = voice.Speak(said); }
                catch (VoiceException error) { problem = Mac(error.Message); }
                catch (Exception error) { problem = Voice.Short("Voice glitched: " + error.Message); }
                Dispatcher.UIThread.Post(delegate
                {
                    voiceProblem = problem;
                    if (ticket != asking || shown[0]) return;
                    shown[0] = true;
                    double seconds = wav == null ? 0 : Voice.Seconds(wav);
                    Talk.Answer(heard, said, seconds);
                    if (seconds >= 0.2) voice.Play(wav);
                    else TypeAlong(said, ticket);
                });
            });
        }

        private static string Mac(string problem) { return problem == Voice.Rejected ? "ElevenLabs rejected the key. Settings > Voice > New key." : problem; }

        private string KeyFile { get { return Path.Combine(Program.Smoke ?? folder, "voice-key"); } }

        private string ReadVoiceKey()
        {
            try
            {
                string key = File.ReadAllText(KeyFile).Trim();
                return key.Length == 0 ? null : key;
            }
            catch (Exception) { return null; }
        }

        private void WriteVoiceKey(string key)
        {
            string path = KeyFile;
            try
            {
                if (key == null) { File.Delete(path); return; }
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                FileStreamOptions options = new FileStreamOptions { Mode = FileMode.Create, Access = FileAccess.Write };
                if (!OperatingSystem.IsWindows()) options.UnixCreateMode = UnixFileMode.UserRead | UnixFileMode.UserWrite;
                using (FileStream stream = new FileStream(path, options))
                {
                    if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(stream.SafeFileHandle, UnixFileMode.UserRead | UnixFileMode.UserWrite);
                    using (StreamWriter writer = new StreamWriter(stream)) writer.Write(key);
                }
            }
            catch (Exception error) { Brain.Log(path, -1, "Couldn't save the voice key: " + error.Message); }
        }

        internal void AskVoiceKey()
        {
            ComeBack();
            Talk.AskKey("Paste your ElevenLabs API key and press Enter. It stays on this computer.");
        }

        internal void SaveVoiceKey(string key)
        {
            key = (key ?? "").Trim();
            if (key.Length == 0) return;
            int ticket = ++asking;
            voice.Stop();
            Talk.Thinking(KeyHeard);
            ThreadPool.QueueUserWorkItem(delegate
            {
                VoiceListing found = null;
                string problem = null;
                int status = 0;
                try { found = Voice.Fetch(key); }
                catch (VoiceException error) { problem = error.Message; status = error.Status; }
                catch (Exception error) { problem = Voice.Short("Couldn't read your ElevenLabs voices: " + error.Message); }
                Dispatcher.UIThread.Post(delegate
                {
                    if (status == 401) { if (ticket == asking) Talk.Answer(KeyHeard, Voice.Unsaved(problem)); return; }
                    voice.Key = key;
                    WriteVoiceKey(key);
                    voiceProblem = problem == null ? null : Mac(problem);
                    if (found != null)
                    {
                        voices = found;
                        if (found.Voices.Count > 0 && (voice.VoiceId == null || !Lists(found, voice.VoiceId))) voice.VoiceId = found.Voices[0].Id;
                    }
                    Saved.VoiceId = voice.VoiceId;
                    Saved.Silent = false;
                    Save();
                    if (ticket != asking) return;
                    if (problem != null) Talk.Answer(KeyHeard, "Key saved, but " + voiceProblem);
                    else if (voice.VoiceId == null) Talk.Answer(KeyHeard, "Key saved, but your ElevenLabs account has no voices yet. Add one at elevenlabs.io.");
                    else Reply(ticket, KeyHeard, "Key saved. I'll talk as " + VoiceName() + ". Change it under Settings > Voice.");
                });
            });
        }

        private static bool Lists(VoiceListing found, string id)
        {
            foreach (VoiceEntry entry in found.Voices) if (entry.Id == id) return true;
            return false;
        }

        private string VoiceName()
        {
            if (voice.VoiceId == null) return null;
            if (voices != null) foreach (VoiceEntry entry in voices.Voices) if (entry.Id == voice.VoiceId && !string.IsNullOrEmpty(entry.Name)) return entry.Name;
            return voice.VoiceId;
        }

        internal string VoiceStatus { get { return (voice.Key == null ? "no key" : "key " + Voice.Tail(voice.Key)) + " | " + (VoiceName() ?? "no voice") + " | " + (Speaks ? "speaking" : "quiet"); } }

        internal bool VoiceWorking { get { return Speaks && voiceProblem == null; } }

        internal bool TypingSound { get { return Saved.Typing == 1 || (Saved.Typing == 0 && !VoiceWorking); } }

        internal void SetTyping(bool on)
        {
            Saved.Typing = on ? 1 : 2;
            Sfx.Typing = on;
            Save();
            Sfx.Type("click clack", Talk.LettersPerSecond, 1);
        }

        internal void AutoTyping() { Saved.Typing = 0; }

        private void TypeAlong(string said, int ticket)
        {
            Sfx.Typing = TypingSound;
            Sfx.Type(said, Talk.Pace, ticket);
        }

        internal void SetSpeak(bool on)
        {
            Saved.Silent = !on;
            if (!on) voice.Stop();
            Save();
        }

        internal void ChooseVoice(string id)
        {
            voice.VoiceId = id;
            Saved.VoiceId = id;
            Save();
        }

        internal void ForgetVoice()
        {
            voice.Stop();
            voice.Key = null;
            voices = null;
            voiceProblem = null;
            WriteVoiceKey(null);
        }

        private void ListVoices()
        {
            string key = voice.Key;
            if (key == null || listing) return;
            listing = true;
            ThreadPool.QueueUserWorkItem(delegate
            {
                VoiceListing found = null;
                string problem = null;
                try { found = Voice.Fetch(key); }
                catch (VoiceException error) { problem = Mac(error.Message); }
                catch (Exception error) { problem = Voice.Short("Couldn't read your ElevenLabs voices: " + error.Message); }
                Dispatcher.UIThread.Post(delegate
                {
                    listing = false;
                    if (voice.Key != key) return;
                    if (found != null) voices = found;
                    voiceProblem = problem;
                    RefreshVoice();
                });
            });
        }

        internal string VoiceMenu
        {
            get
            {
                RefreshVoice();
                List<string> shown = new List<string>();
                foreach (object item in voiceMenu.Items)
                {
                    MenuItem entry = item as MenuItem;
                    if (entry == null) shown.Add("-");
                    else shown.Add((entry.IsChecked ? "*" : "") + entry.Header + (entry.IsEnabled ? "" : " (off)"));
                }
                return string.Join(" | ", shown);
            }
        }

        private void RefreshVoice()
        {
            if (voiceMenu == null) return;
            voiceMenu.Items.Clear();
            if (voice.Key == null)
            {
                MenuItem add = new MenuItem { Header = "Add ElevenLabs key..." };
                add.Click += delegate { AskVoiceKey(); };
                voiceMenu.Items.Add(add);
                return;
            }
            MenuItem speak = new MenuItem { Header = "Use ElevenLabs", ToggleType = MenuItemToggleType.CheckBox, IsChecked = Speaks, IsEnabled = voice.VoiceId != null };
            speak.Click += delegate { SetSpeak(Saved.Silent); };
            voiceMenu.Items.Add(speak);
            voiceMenu.Items.Add(new Separator());
            if (voices == null)
            {
                voiceMenu.Items.Add(new MenuItem { Header = "Loading voices...", IsEnabled = false });
                ListVoices();
            }
            else
            {
                int shown = 0;
                foreach (VoiceEntry entry in voices.Voices)
                {
                    if (shown++ >= 30) break;
                    string id = entry.Id;
                    MenuItem pick = new MenuItem { Header = string.IsNullOrEmpty(entry.Name) ? id : entry.Name, ToggleType = MenuItemToggleType.Radio, GroupName = "voice", IsChecked = id == voice.VoiceId };
                    pick.Click += delegate { ChooseVoice(id); };
                    voiceMenu.Items.Add(pick);
                }
                if (voices.Voices.Count == 0) voiceMenu.Items.Add(new MenuItem { Header = "No voices on this key", IsEnabled = false });
            }
            voiceMenu.Items.Add(new Separator());
            MenuItem renew = new MenuItem { Header = "New key..." };
            renew.Click += delegate { AskVoiceKey(); };
            MenuItem remove = new MenuItem { Header = "Remove key ending " + Voice.Tail(voice.Key) };
            remove.Click += delegate { ForgetVoice(); };
            voiceMenu.Items.Add(renew);
            voiceMenu.Items.Add(remove);
            if (voiceProblem != null) voiceMenu.Items.Add(new MenuItem { Header = voiceProblem, IsEnabled = false });
        }

        private Settings Load()
        {
            try
            {
                using (FileStream stream = File.OpenRead(Path.Combine(folder, "cross.json"))) return (Settings)new DataContractJsonSerializer(typeof(Settings)).ReadObject(stream);
            }
            catch (Exception) { return new Settings(); }
        }

        internal void Save()
        {
            Saved.X = anchor.X;
            Saved.Y = anchor.Y;
            Saved.Placed = true;
            if (Program.Smoke != null) return;
            try
            {
                Directory.CreateDirectory(folder);
                using (FileStream stream = File.Create(Path.Combine(folder, "cross.json"))) new DataContractJsonSerializer(typeof(Settings)).WriteObject(stream, Saved);
            }
            catch (Exception) { }
        }

        internal void Quit()
        {
            if (quitting) return;
            quitting = true;
            Brain.Cancel();
            voice.Stop();
            Talk.Dismiss();
            GlitchOut(delegate
            {
                Save();
                frames.Stop();
                desktop.Shutdown(0);
            });
        }

        internal void Finish(int code)
        {
            frames.Stop();
            Talk.Dismiss();
            desktop.Shutdown(code);
        }
    }
}
